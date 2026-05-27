"""Workspace working-tree diff against HEAD.

Exposed via `GET /_gapt/api/workspaces/{wid}/diff` so the Web IDE's
`DiffPanel` can show what the user (or the agent) has changed since
the last commit. Backed by `SandboxBackend.exec_in` so the operation
runs inside the same sandbox image the host clone landed in.

We return a small structured payload:

    {
      "files": [{"path": "...", "status": "M", "additions": 3, "deletions": 1}, ...],
      "unified": "diff --git a/... b/...\\n...",
      "truncated": false
    }

`status` is a single-letter code straight from `git diff --name-status`
(`M` modified · `A` added · `D` deleted · `R` rename target · `C` copy
target · `T` type change) — *plus* the synthetic `U` for untracked
files we surface separately (git's porcelain does not list them in the
diff). The `unified` blob is capped at `MAX_DIFF_BYTES` so a runaway
worktree does not push us into a multi-MB response.
"""

from __future__ import annotations

from dataclasses import dataclass

from gapt_server.domains.sandbox import SandboxBackend, SandboxBackendError, SandboxRef

MAX_DIFF_BYTES = 256 * 1024
MAX_FILES = 500


class WorkspaceDiffError(RuntimeError):
    """Stable code suffix surfaces to the router as HTTP."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DiffFileEntry:
    path: str
    status: str  # "M" | "A" | "D" | "R" | "C" | "T" | "U"
    additions: int
    deletions: int


@dataclass(frozen=True)
class DiffResult:
    files: list[DiffFileEntry]
    unified: str
    truncated: bool


async def _exec(
    backend: SandboxBackend, ref: SandboxRef, argv: list[str]
) -> tuple[int, str, str]:
    try:
        result = await backend.exec_in(ref, argv)
    except SandboxBackendError as exc:
        raise WorkspaceDiffError(
            "workspace.diff.exec_failed", f"sandbox exec failed: {exc}"
        ) from exc
    return (
        result.exit_code,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def _parse_numstat(text: str) -> dict[str, tuple[int, int]]:
    """`git diff --numstat HEAD` rows are `<add>\t<del>\t<path>`. For
    binary files git emits `-\t-\t<path>` — surface those as (0, 0).
    """
    out: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        add_s, del_s, path = parts
        try:
            adds = int(add_s) if add_s != "-" else 0
        except ValueError:
            adds = 0
        try:
            dels = int(del_s) if del_s != "-" else 0
        except ValueError:
            dels = 0
        out[path] = (adds, dels)
    return out


def _parse_name_status(text: str) -> list[tuple[str, str]]:
    """`git diff --name-status HEAD` rows are `<code>\t<path>` (renames
    have `\t<old>\t<new>` — we surface the new path)."""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0][:1] or "M"
        path = parts[-1]
        rows.append((code, path))
    return rows


async def working_tree_diff(
    backend: SandboxBackend,
    ref: SandboxRef,
    *,
    worktree_path: str,
) -> DiffResult:
    """Return the working-tree-vs-HEAD diff for a workspace."""

    # 1. Quick check that this is in fact a git worktree. If not, the
    #    UI should treat the worktree as "nothing to compare" rather
    #    than surfacing a confusing git error.
    is_git_exit, _, _ = await _exec(
        backend, ref, ["git", "-C", worktree_path, "rev-parse", "--git-dir"]
    )
    if is_git_exit != 0:
        return DiffResult(files=[], unified="", truncated=False)

    name_status_exit, name_status_out, name_status_err = await _exec(
        backend, ref, ["git", "-C", worktree_path, "diff", "HEAD", "--name-status"]
    )
    if name_status_exit != 0:
        # Empty repo (no HEAD yet) returns non-zero — surface as empty
        # rather than a 500. Don't include the raw stderr in the error
        # path because it can leak local paths.
        if "unknown revision" in name_status_err or "bad revision" in name_status_err:
            return DiffResult(files=[], unified="", truncated=False)
        raise WorkspaceDiffError(
            "workspace.diff.name_status_failed", name_status_err.strip()[:200]
        )
    rows = _parse_name_status(name_status_out)[:MAX_FILES]

    _, numstat_out, _ = await _exec(
        backend, ref, ["git", "-C", worktree_path, "diff", "HEAD", "--numstat"]
    )
    numstat = _parse_numstat(numstat_out)

    entries = [
        DiffFileEntry(
            path=path,
            status=code,
            additions=numstat.get(path, (0, 0))[0],
            deletions=numstat.get(path, (0, 0))[1],
        )
        for code, path in rows
    ]

    # Untracked files — surface them as synthetic "U" entries so the
    # user can see them in the panel without having to git-add first.
    _, untracked_out, _ = await _exec(
        backend,
        ref,
        [
            "git",
            "-C",
            worktree_path,
            "ls-files",
            "--others",
            "--exclude-standard",
        ],
    )
    seen = {e.path for e in entries}
    for path in untracked_out.splitlines():
        path = path.strip()
        if not path or path in seen:
            continue
        entries.append(DiffFileEntry(path=path, status="U", additions=0, deletions=0))
        if len(entries) >= MAX_FILES:
            break

    # The big unified blob — capped. We pass `--unified=3` (default but
    # explicit) and `--no-color` so the bytes are stable across user
    # environments. Untracked content is not included; the UI shows the
    # untracked list separately.
    _, unified_out, _ = await _exec(
        backend,
        ref,
        [
            "git",
            "-C",
            worktree_path,
            "diff",
            "HEAD",
            "--unified=3",
            "--no-color",
        ],
    )
    truncated = False
    if len(unified_out.encode("utf-8")) > MAX_DIFF_BYTES:
        # Hard byte cap; cut at the next newline so we don't split a
        # codepoint or a hunk header in the middle.
        sliced = unified_out.encode("utf-8")[:MAX_DIFF_BYTES]
        # Walk back to the last newline so the marker lands clean.
        last_nl = sliced.rfind(b"\n")
        if last_nl > 0:
            sliced = sliced[:last_nl]
        unified_out = sliced.decode("utf-8", errors="replace") + "\n[…truncated by gapt diff cap…]\n"
        truncated = True

    return DiffResult(files=entries, unified=unified_out, truncated=truncated)
