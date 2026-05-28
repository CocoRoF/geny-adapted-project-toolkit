"""Bare-repo + `git worktree` lifecycle for the Phase C.1 workspace
model.

Layout per project (rooted at the host's `${WORKSPACE_ROOT}`):

    /workspace/<project_slug>/
        .bare/                 ← bare clone of project.git_remote_url
        <workspace_id>/        ← a real `git worktree` of .bare
        <other_workspace_id>/  ← another worktree, different branch

The bare repo is shared between every workspace of the project so
fetching `origin` once updates all branches and worktrees see the
new refs immediately. Object storage is shared — adding a 10th
worktree costs only the working-tree files, not another full clone.

Compared to the old `git clone --depth=1 --branch=<x>` per workspace
this gives us:
  - O(repo) disk instead of O(repo × workspaces)
  - Cheap branch-switching (a new workspace == one `worktree add`)
  - A single place to pull (`git -C .bare fetch origin --prune`)

Legacy workspaces (created before Phase C.1) are plain clones inside
their `worktree_path` dir with no bare-repo link. The delete path
below detects that shape and removes the dir directly without trying
`git worktree remove` (which would fail with "not a worktree").
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


def _bare_dir(project_root: str) -> str:
    return os.path.join(project_root, ".bare")


def is_bare_initialized(project_root: str) -> bool:
    """True when `<project_root>/.bare` exists and looks like a real
    bare repo. We check for `HEAD` rather than `.git` since bare repos
    *are* the .git dir — they have HEAD at the top level."""
    bare = _bare_dir(project_root)
    return os.path.isfile(os.path.join(bare, "HEAD"))


def is_worktree_path(worktree: str) -> bool:
    """True when `<worktree>/.git` is a *file* (not a dir) — that's
    the marker of a git worktree (the file contains a `gitdir:` line
    pointing back to the bare repo's `worktrees/<id>` metadata)."""
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
    project_root: str,
    *,
    git_remote_url: str,
    extra_config: list[str] | None = None,
) -> GitRunResult:
    """Idempotent: create `<project_root>/.bare` via `git clone --bare`
    on first call, or fetch updates on subsequent calls.

    `extra_config` carries the same `-c http.extraHeader=...` flags the
    clone path uses, so private-repo auth survives.
    """
    Path(project_root).mkdir(parents=True, exist_ok=True)
    bare = _bare_dir(project_root)
    if is_bare_initialized(project_root):
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
    project_root: str,
    *,
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
    bare = _bare_dir(project_root)
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
    project_root: str,
    *,
    worktree_path: str,
) -> GitRunResult:
    """Best-effort cleanup. `git worktree remove --force` deletes the
    working-tree dir *and* the metadata entry under `.bare/worktrees/`.
    Falls back to `rmtree` for legacy non-worktree dirs."""
    if not os.path.isdir(worktree_path):
        return GitRunResult(exit_code=0, stdout="", stderr="worktree path absent")

    if is_bare_initialized(project_root) and is_worktree_path(worktree_path):
        bare = _bare_dir(project_root)
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
            # `worktree remove` already pruned metadata; nothing else
            # to do.
            return result
        # Fallthrough: force-rmtree below + best-effort prune.

    # Legacy path or worktree-remove failed. rm -rf the dir then ask
    # git to clean up any dangling metadata.
    try:
        await asyncio.to_thread(shutil.rmtree, worktree_path, True)
    except OSError as exc:
        return GitRunResult(
            exit_code=-1, stdout="", stderr=f"rmtree failed: {exc}"
        )
    if is_bare_initialized(project_root):
        await _run_git(
            ["-C", _bare_dir(project_root), "worktree", "prune"],
        )
    return GitRunResult(exit_code=0, stdout="", stderr="cleaned (legacy path)")
