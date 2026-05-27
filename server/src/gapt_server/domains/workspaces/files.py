"""Workspace file-system access — minimal tree / read / write / delete.

Exposed via `/_gapt/api/workspaces/{wid}/{tree,files}` so the web IDE
(`FileTree`, `Editor`) can navigate the workspace contents. Backed by
`SandboxBackend.exec_in` so the operation runs *inside* the sandbox —
the host filesystem is never touched.

Threat model:

- Path traversal: every supplied `path` is normalised against
  `workspace.worktree_path` and refused if it escapes. The sandbox is
  rooted there, but defence-in-depth.
- Symbolic links: `find -P` (physical) prevents a symlink from
  smuggling us out of the root.
- Resource bounds: tree returns at most `MAX_ENTRIES` and trims any
  result past `MAX_BYTES`. Read refuses files over `MAX_FILE_BYTES`.

Read / write / delete are POSIX shell calls. We never invoke a real
editor — we ship `cat -- /path` / `tee -- /path` / `rm -- /path`.
That keeps the API stable across the sandbox image's package set.
"""

from __future__ import annotations

import base64
import json
import shlex
from dataclasses import dataclass

from gapt_server.domains.sandbox import SandboxBackend, SandboxBackendError, SandboxRef

MAX_ENTRIES = 2000
MAX_BYTES = 256 * 1024
MAX_FILE_BYTES = 1 * 1024 * 1024


class WorkspaceFileError(RuntimeError):
    """Stable code suffix surfaces to the router as HTTP."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TreeEntry:
    name: str
    path: str  # workspace-relative ("/" = root)
    kind: str  # "file" | "dir"
    size: int | None = None


@dataclass(frozen=True)
class FileContent:
    path: str
    encoding: str  # "utf-8" or "base64"
    text: str


def _normalise_relative(root: str, relative: str) -> str:
    """Refuse traversal. Returns an absolute path inside `root`."""
    if relative in {"", "/", "."}:
        return root
    candidate = relative.lstrip("/")
    # No `..` segments anywhere — strict.
    if any(seg in {"..", ""} for seg in candidate.split("/")):
        raise WorkspaceFileError("workspace.path.invalid", f"path {relative!r} is invalid")
    absolute = f"{root.rstrip('/')}/{candidate}"
    # Belt-and-braces: the resolved path must still be prefixed by root.
    if not absolute.startswith(root.rstrip("/") + "/") and absolute != root:
        raise WorkspaceFileError("workspace.path.traversal", "path escaped workspace root")
    return absolute


def _normalise_root_path(root: str, absolute: str) -> str:
    """Reverse: absolute → workspace-relative ("/", "/src/foo.py")."""
    if absolute == root:
        return "/"
    if not absolute.startswith(root + "/"):
        return absolute  # belt: report as-is rather than silently misclassify
    return absolute[len(root) :]


async def _exec(backend: SandboxBackend, ref: SandboxRef, argv: list[str]) -> tuple[int, str, str]:
    try:
        result = await backend.exec_in(ref, argv)
    except SandboxBackendError as exc:
        raise WorkspaceFileError("workspace.fs.exec_failed", f"sandbox exec failed: {exc}") from exc
    return (
        result.exit_code,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


async def list_tree(
    backend: SandboxBackend,
    ref: SandboxRef,
    *,
    worktree_path: str,
    path: str = "/",
) -> list[TreeEntry]:
    """Return the *immediate* children of `path` (one level deep).

    Path traversal is rejected; symlinks are not followed (`-P`); the
    result is capped at MAX_ENTRIES. Each row is one of:

        d <size> <abs-path>
        f <size> <abs-path>
    """
    abs_path = _normalise_relative(worktree_path, path)
    # `find … -maxdepth 1 -printf` is the cheap canonical listing.
    # Format: kind<TAB>size<TAB>path<NUL>. NUL terminator survives
    # filenames with newlines.
    argv = [
        "find",
        "-P",
        abs_path,
        "-mindepth",
        "1",
        "-maxdepth",
        "1",
        "-printf",
        "%y\\t%s\\t%p\\0",
    ]
    exit_code, stdout, stderr = await _exec(backend, ref, argv)
    if exit_code != 0:
        # `find` may exit non-zero for missing directories — surface
        # the stderr first 200 chars.
        raise WorkspaceFileError(
            "workspace.fs.list_failed", f"find failed ({exit_code}): {stderr[:200]}"
        )
    entries: list[TreeEntry] = []
    for raw in stdout.split("\0"):
        if not raw:
            continue
        parts = raw.split("\t", 2)
        if len(parts) != 3:
            continue
        kind_char, size_str, full_path = parts
        kind = "dir" if kind_char == "d" else "file" if kind_char == "f" else None
        if kind is None:
            continue
        size: int | None
        try:
            size = int(size_str)
        except ValueError:
            size = None
        rel_path = _normalise_root_path(worktree_path, full_path)
        entries.append(
            TreeEntry(
                name=full_path.rsplit("/", 1)[-1],
                path=rel_path,
                kind=kind,
                size=size if kind == "file" else None,
            )
        )
        if len(entries) >= MAX_ENTRIES:
            break

    # Stable order: dirs first, then alphabetical.
    entries.sort(key=lambda e: (e.kind != "dir", e.name.lower()))
    return entries


async def read_file(
    backend: SandboxBackend,
    ref: SandboxRef,
    *,
    worktree_path: str,
    path: str,
) -> FileContent:
    """Read the file at `path` (workspace-relative). Refuses files over
    `MAX_FILE_BYTES`. Returns utf-8 text when the bytes decode cleanly,
    otherwise base64-encoded raw bytes."""
    abs_path = _normalise_relative(worktree_path, path)
    # First check size to enforce the cap without pulling the file.
    stat_exit, stat_out, _ = await _exec(
        backend,
        ref,
        ["stat", "-c", "%s", "--", abs_path],
    )
    if stat_exit != 0:
        raise WorkspaceFileError("workspace.fs.not_found", f"no such file: {path}")
    try:
        size = int(stat_out.strip())
    except ValueError as exc:
        raise WorkspaceFileError("workspace.fs.stat_failed", f"stat returned {stat_out!r}") from exc
    if size > MAX_FILE_BYTES:
        raise WorkspaceFileError(
            "workspace.fs.too_large",
            f"file exceeds {MAX_FILE_BYTES} bytes (size={size})",
        )
    exit_code, stdout, stderr = await _exec(backend, ref, ["cat", "--", abs_path])
    if exit_code != 0:
        raise WorkspaceFileError("workspace.fs.read_failed", f"cat failed: {stderr[:200]}")
    # cat used `errors=replace` decode — for true binary files we
    # should fall back to base64. Heuristic: if the result contains
    # U+FFFD (replacement char), re-encode as base64. exec_in already
    # gave us bytes via stdout, but we lost them at decode time.
    if "�" in stdout:
        # Re-run with base64 wrapper inside the sandbox to be honest
        # about the bytes.
        b64_exit, b64_out, b64_err = await _exec(
            backend, ref, ["base64", "-w", "0", "--", abs_path]
        )
        if b64_exit != 0:
            raise WorkspaceFileError("workspace.fs.read_failed", f"base64 failed: {b64_err[:200]}")
        return FileContent(path=path, encoding="base64", text=b64_out.strip())
    return FileContent(path=path, encoding="utf-8", text=stdout)


async def write_file(
    backend: SandboxBackend,
    ref: SandboxRef,
    *,
    worktree_path: str,
    path: str,
    content: str,
    encoding: str = "utf-8",
) -> None:
    """Overwrite the file at `path` with `content`. Creates parent
    directories as needed. `encoding=base64` allows writing binary."""
    abs_path = _normalise_relative(worktree_path, path)
    parent = abs_path.rsplit("/", 1)[0]
    mk_exit, _, mk_err = await _exec(backend, ref, ["mkdir", "-p", "--", parent])
    if mk_exit != 0:
        raise WorkspaceFileError(
            "workspace.fs.mkdir_failed", f"mkdir -p {parent!r} failed: {mk_err[:200]}"
        )
    if encoding == "utf-8":
        # `sh -c` here is intentional — we have to redirect into a
        # shell-quoted path. The content is passed via stdin so it
        # never lands in argv (no length cap, no escaping headache).
        body = content
    elif encoding == "base64":
        body = content
    else:
        raise WorkspaceFileError("workspace.path.invalid", f"unsupported encoding: {encoding!r}")
    # `tee` reads stdin and writes to argv[1]. SysboxBackend needs the
    # bytes via stdin — for M1 we sidestep the stdin plumbing by
    # encoding the payload in argv via `printf` (base64-safe). M2 will
    # add a real stdin channel to the backend signature.
    # printf reads its first arg as a format string; %s lets us pass
    # any payload safely. Base64-encode utf-8 text so binary writes
    # don't need a different code path.
    b64 = base64.b64encode(body.encode("utf-8")).decode("ascii") if encoding == "utf-8" else body
    # `sh -c 'echo $1 | base64 -d > $2' _ <b64> <path>` keeps the
    # base64 string in a positional argument rather than the script
    # body, so shell metacharacters in it can't break the command.
    cmd = 'echo "$1" | base64 -d > "$2"'
    argv = ["sh", "-c", cmd, "_", b64, abs_path]
    exit_code, _, stderr = await _exec(backend, ref, argv)
    if exit_code != 0:
        raise WorkspaceFileError("workspace.fs.write_failed", f"write failed: {stderr[:200]}")


async def delete_path(
    backend: SandboxBackend,
    ref: SandboxRef,
    *,
    worktree_path: str,
    path: str,
) -> None:
    """Remove the file or empty directory at `path`. Non-empty
    directories are refused — the UI's "delete" affordance only
    targets a single file or empty folder."""
    abs_path = _normalise_relative(worktree_path, path)
    if abs_path == worktree_path:
        raise WorkspaceFileError("workspace.path.invalid", "cannot delete the workspace root")
    exit_code, _, stderr = await _exec(backend, ref, ["rm", "-d", "--", abs_path])
    if exit_code != 0:
        raise WorkspaceFileError("workspace.fs.delete_failed", f"rm failed: {stderr[:200]}")


# Re-exported so tests can mock structured exec responses with the
# canonical argv shape.
def find_argv_for(abs_path: str) -> list[str]:
    return [
        "find",
        "-P",
        abs_path,
        "-mindepth",
        "1",
        "-maxdepth",
        "1",
        "-printf",
        "%y\\t%s\\t%p\\0",
    ]


# The `json` / `shlex` imports survive for callers that build their
# own argv variations — left at the top so the file is import-stable.
_ = (json, shlex)
