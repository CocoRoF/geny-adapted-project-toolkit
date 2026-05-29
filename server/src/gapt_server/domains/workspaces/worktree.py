"""Bare-repo + `git worktree` lifecycle for the Phase C.1 workspace
model.

Layout split across two roots:

    <workspace_root>/<project_slug>/        ← worktree dir (user content)
        <workspace_id>/                     ← a real `git worktree`
        <other_workspace_id>/               ← another worktree

    <workspace_bare_root>/<project_slug>/   ← bare clone (gapt-managed)
        HEAD, objects/, refs/, worktrees/<wid>/...

The bare lives in a separate root from the worktree so the bare's
parent dirs don't show up as untracked entries inside the worktree
when the workspace container mounts both. Two paths (one per role)
keep the GAPT-managed and user-visible content cleanly disjoint.

The bare repo is shared between every workspace of the project so
fetching `origin` once updates all branches. Object storage is
shared — adding a 10th worktree costs only the working-tree files,
not another full clone.

Legacy workspaces (Phase C.1 vintage) have their bare at
`<project_root>/.bare/` instead. The lifespan migration in
`gapt_server.app` moves them to the new layout at server start.
Plain pre-C.1 clones (no bare at all) are detected by
`is_worktree_path` returning False and stay untouched.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


_GIT_BIN = shutil.which("git") or "/usr/bin/git"
_WORKTREE_TIMEOUT_S = 600.0
_FETCH_TIMEOUT_S = 600.0


def bare_dir(bare_root: str, project_slug: str) -> str:
    """`<bare_root>/<project_slug>/` — the per-project bare repo path.

    Public so callers (workspace service, sandbox manager, migration)
    can derive the bare path identically without re-implementing the
    join logic."""
    return os.path.join(bare_root, project_slug)


def legacy_bare_dir(project_root: str) -> str:
    """Pre-2026-05-29 location, kept as a constant so the migration
    can find it. New code never writes here."""
    return os.path.join(project_root, ".bare")


def is_bare_initialized(bare_root: str, project_slug: str) -> bool:
    """True when `<bare_root>/<project_slug>/HEAD` exists — that's
    the cheapest sentinel for "this dir is a real bare repo" without
    spawning git."""
    return os.path.isfile(os.path.join(bare_dir(bare_root, project_slug), "HEAD"))


def is_worktree_path(worktree: str) -> bool:
    """True when `<worktree>/.git` is a *file* (not a dir) — that's
    the marker of a git worktree (the file contains a `gitdir:` line
    pointing at the bare's `worktrees/<wid>/` metadata)."""
    return os.path.isfile(os.path.join(worktree, ".git"))


@dataclass(frozen=True)
class GitRunResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


async def _run_git(
    args: list[str], *, cwd: str | None = None, timeout_s: float = _WORKTREE_TIMEOUT_S
) -> GitRunResult:
    """Run `git <args>` and capture stdout/stderr. Used by every
    worktree primitive in this module."""
    proc = await asyncio.create_subprocess_exec(
        _GIT_BIN,
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return GitRunResult(
            exit_code=-1,
            stdout="",
            stderr=f"git {' '.join(args)} timed out after {timeout_s}s",
        )
    return GitRunResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )


async def ensure_bare(
    *,
    bare_root: str,
    project_slug: str,
    git_remote_url: str,
    extra_config: list[str] | None = None,
) -> GitRunResult:
    """Idempotent: create `<bare_root>/<project_slug>/` via
    `git clone --bare` on first call, or fetch updates on subsequent
    calls.

    `extra_config` carries the `-c http.extraHeader=...` flags the
    clone path uses, so private-repo auth survives.
    """
    Path(bare_root).mkdir(parents=True, exist_ok=True)
    bare = bare_dir(bare_root, project_slug)
    if is_bare_initialized(bare_root, project_slug):
        # Update refs — quiet but force-prune deleted upstream
        # branches so stale ones don't linger as workspace candidates.
        return await _run_git(
            [
                *(extra_config or []),
                "-C",
                bare,
                "fetch",
                "origin",
                "--prune",
                "--quiet",
            ],
            timeout_s=_FETCH_TIMEOUT_S,
        )
    # First-time clone. We want all branches, not just HEAD, so
    # `git worktree add origin/<any-branch>` works later.
    return await _run_git(
        [
            *(extra_config or []),
            "clone",
            "--bare",
            "--quiet",
            git_remote_url,
            bare,
        ],
        timeout_s=_FETCH_TIMEOUT_S,
    )


async def add_worktree(
    *,
    bare_root: str,
    project_slug: str,
    worktree_path: str,
    branch: str,
) -> GitRunResult:
    """Create a worktree for `<branch>` at `<worktree_path>`.

    Branch resolution, in priority order:

    1. **Local ref `refs/heads/<branch>` exists** in the bare. This is
       the common case for a freshly-`clone --bare`-d repo, which
       mirrors every upstream branch under `refs/heads/`. Just check
       it out — no `-b` (would refuse) and no `-B` (would reset).
    2. **Remote-tracking `refs/remotes/origin/<branch>` exists**.
       Happens when the bare was created via `git init --bare` +
       configured remotes rather than `clone --bare`. Use `-B` to
       materialise the local branch from the remote ref.
    3. **Neither exists** — brand-new branch. Create it from HEAD via
       `-b`. Matches the "start a feature on a fresh branch" intent.
    """
    bare = bare_dir(bare_root, project_slug)
    local_ref = await _run_git(
        ["-C", bare, "rev-parse", "--verify", f"refs/heads/{branch}"],
    )
    if local_ref.ok:
        return await _run_git(
            ["-C", bare, "worktree", "add", worktree_path, branch],
        )
    remote_ref = await _run_git(
        ["-C", bare, "rev-parse", "--verify", f"refs/remotes/origin/{branch}"],
    )
    if remote_ref.ok:
        return await _run_git(
            [
                "-C",
                bare,
                "worktree",
                "add",
                "-B",
                branch,
                worktree_path,
                f"origin/{branch}",
            ],
        )
    return await _run_git(
        [
            "-C",
            bare,
            "worktree",
            "add",
            "-b",
            branch,
            worktree_path,
            "HEAD",
        ],
    )


async def remove_worktree(
    *,
    bare_root: str,
    project_slug: str,
    worktree_path: str,
) -> GitRunResult:
    """Best-effort cleanup. `git worktree remove --force` deletes the
    working-tree dir *and* the metadata entry under
    `<bare>/worktrees/`. Falls back to `rmtree` for legacy non-worktree
    dirs."""
    if not os.path.isdir(worktree_path):
        return GitRunResult(exit_code=0, stdout="", stderr="worktree path absent")

    if is_bare_initialized(bare_root, project_slug) and is_worktree_path(worktree_path):
        bare = bare_dir(bare_root, project_slug)
        result = await _run_git(
            [
                "-C",
                bare,
                "worktree",
                "remove",
                "--force",
                worktree_path,
            ],
        )
        if result.ok:
            return result
        # Fallthrough: force-rmtree below + best-effort prune.

    try:
        await asyncio.to_thread(shutil.rmtree, worktree_path, True)
    except OSError as exc:
        return GitRunResult(
            exit_code=-1, stdout="", stderr=f"rmtree failed: {exc}"
        )
    if is_bare_initialized(bare_root, project_slug):
        await _run_git(
            ["-C", bare_dir(bare_root, project_slug), "worktree", "prune"],
        )
    return GitRunResult(exit_code=0, stdout="", stderr="cleaned (legacy path)")


# ───────────────────────────────────── migration helpers ──


def read_worktree_gitdir(worktree_path: str) -> str | None:
    """Parse `<worktree>/.git` (a file in worktree layout) and return
    the absolute path it references. None when `.git` isn't a file or
    the content is malformed."""
    git_file = os.path.join(worktree_path, ".git")
    if not os.path.isfile(git_file):
        return None
    try:
        with open(git_file, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return None
    for line in content.splitlines():
        if line.startswith("gitdir:"):
            return line[len("gitdir:") :].strip()
    return None


def derive_bare_from_gitdir(gitdir: str) -> str | None:
    """`gitdir` is the absolute path stored in a worktree's `.git`
    file — shape `<bare>/worktrees/<wid>`. Strip the `worktrees/<wid>`
    suffix to recover the bare directory. None when the shape doesn't
    match (legacy clone written by hand, etc.)."""
    parts = gitdir.rstrip("/").split("/")
    if len(parts) < 3 or parts[-2] != "worktrees":
        return None
    return "/".join(parts[:-2])


def rewrite_worktree_gitdir(worktree_path: str, new_gitdir: str) -> None:
    """Replace the `.git` file's `gitdir:` line with `new_gitdir`. Used
    by the migration to point worktrees at the moved bare. Writes the
    canonical `gitdir: <path>\\n` format git itself emits."""
    git_file = os.path.join(worktree_path, ".git")
    with open(git_file, "w", encoding="utf-8") as fh:
        fh.write(f"gitdir: {new_gitdir}\n")


def rewrite_bare_worktree_pointer(bare_dir_path: str, workspace_id: str, worktree_path: str) -> None:
    """Update `<bare>/worktrees/<wid>/gitdir` to point at the
    worktree's `.git` file. Git uses this as the reverse pointer for
    `worktree list` and lock detection. Migration rewrites it when the
    bare moves so the back-reference stays consistent."""
    pointer = os.path.join(bare_dir_path, "worktrees", workspace_id, "gitdir")
    if not os.path.isfile(pointer):
        return
    target = os.path.join(worktree_path, ".git")
    with open(pointer, "w", encoding="utf-8") as fh:
        fh.write(f"{target}\n")
