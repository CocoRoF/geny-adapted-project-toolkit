"""Unit tests for the Phase C.1 bare-repo + worktree primitives.

Uses a real `git` binary against a local fake remote (no network)
so the tests exercise the actual command surface, not a mock. The
fake remote is a fresh bare repo seeded with one commit; tests
create worktrees off it, mutate, and assert disk layout.

As of 2026-05-29 the bare repo lives at
`<bare_root>/<project_slug>/` (a directory separate from the
worktrees) — see `domains/workspaces/worktree.py` module docstring
for the layout rationale.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from gapt_server.domains.workspaces import worktree as worktree_mod


_SLUG = "proj"


def _git(*args: str, cwd: str) -> str:
    """Run git synchronously for fixture setup."""
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout


@pytest.fixture
def fake_remote(tmp_path: Path) -> str:
    """Create a bare repo with one commit on `main` and one on
    `feature/x`. Returns the path usable as a `git_remote_url`."""
    src = tmp_path / "src"
    src.mkdir()
    _git("init", "--initial-branch=main", cwd=str(src))
    _git("config", "user.email", "test@example.com", cwd=str(src))
    _git("config", "user.name", "Test", cwd=str(src))
    (src / "README.md").write_text("hello main\n")
    _git("add", "README.md", cwd=str(src))
    _git("commit", "-m", "init main", cwd=str(src))
    _git("checkout", "-b", "feature/x", cwd=str(src))
    (src / "feature.txt").write_text("on feature\n")
    _git("add", "feature.txt", cwd=str(src))
    _git("commit", "-m", "add feature", cwd=str(src))
    _git("checkout", "main", cwd=str(src))
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(src), str(bare)],
        check=True,
        capture_output=True,
    )
    return str(bare)


@pytest.fixture
def bare_root(tmp_path: Path) -> str:
    """Per-test bare root directory (separate from worktree root)."""
    return str(tmp_path / "bare-root")


@pytest.fixture
def worktree_root(tmp_path: Path) -> str:
    """Per-test worktree root directory.

    The two roots are intentionally NOT siblings of each other — the
    whole point of the 2026-05-29 layout is that the bare lives
    outside the workspace tree so its parent dirs don't pollute the
    worktree's git status when the workspace container mounts both."""
    root = str(tmp_path / "wt-root")
    Path(root).mkdir(parents=True, exist_ok=True)
    return root


async def test_ensure_bare_creates_directory(
    bare_root: str, fake_remote: str
) -> None:
    res = await worktree_mod.ensure_bare(
        bare_root=bare_root, project_slug=_SLUG, git_remote_url=fake_remote
    )
    assert res.ok, res.stderr
    assert worktree_mod.is_bare_initialized(bare_root, _SLUG)
    assert os.path.isfile(
        os.path.join(worktree_mod.bare_dir(bare_root, _SLUG), "HEAD")
    )


async def test_ensure_bare_idempotent_runs_fetch(
    bare_root: str, fake_remote: str
) -> None:
    """Second call must not re-clone — it should fetch updates so the
    operation is cheap and refreshes refs."""
    first = await worktree_mod.ensure_bare(
        bare_root=bare_root, project_slug=_SLUG, git_remote_url=fake_remote
    )
    assert first.ok
    bare_head = os.path.join(worktree_mod.bare_dir(bare_root, _SLUG), "HEAD")
    mtime_before = os.path.getmtime(bare_head)
    second = await worktree_mod.ensure_bare(
        bare_root=bare_root, project_slug=_SLUG, git_remote_url=fake_remote
    )
    assert second.ok, second.stderr
    assert os.path.getmtime(bare_head) == mtime_before


async def test_add_worktree_for_existing_branch(
    bare_root: str, worktree_root: str, fake_remote: str
) -> None:
    await worktree_mod.ensure_bare(
        bare_root=bare_root, project_slug=_SLUG, git_remote_url=fake_remote
    )
    wt = os.path.join(worktree_root, _SLUG, "ws-001")
    res = await worktree_mod.add_worktree(
        bare_root=bare_root,
        project_slug=_SLUG,
        worktree_path=wt,
        branch="feature/x",
    )
    assert res.ok, res.stderr
    assert (Path(wt) / "feature.txt").read_text() == "on feature\n"
    assert worktree_mod.is_worktree_path(wt)


async def test_add_worktree_for_new_branch(
    bare_root: str, worktree_root: str, fake_remote: str
) -> None:
    """A branch that doesn't exist on origin should be created from
    HEAD — used when the operator wants to start a feature on a fresh
    branch from inside GAPT."""
    await worktree_mod.ensure_bare(
        bare_root=bare_root, project_slug=_SLUG, git_remote_url=fake_remote
    )
    wt = os.path.join(worktree_root, _SLUG, "ws-002")
    res = await worktree_mod.add_worktree(
        bare_root=bare_root,
        project_slug=_SLUG,
        worktree_path=wt,
        branch="feature/brand-new",
    )
    assert res.ok, res.stderr
    assert (Path(wt) / "README.md").read_text() == "hello main\n"
    assert not (Path(wt) / "feature.txt").exists()


async def test_two_worktrees_same_bare_share_objects(
    bare_root: str, worktree_root: str, fake_remote: str
) -> None:
    """The whole point: two workspaces on the same project must share
    one bare and only cost their working trees."""
    await worktree_mod.ensure_bare(
        bare_root=bare_root, project_slug=_SLUG, git_remote_url=fake_remote
    )
    wt1 = os.path.join(worktree_root, _SLUG, "ws-A")
    wt2 = os.path.join(worktree_root, _SLUG, "ws-B")
    r1 = await worktree_mod.add_worktree(
        bare_root=bare_root, project_slug=_SLUG, worktree_path=wt1, branch="main"
    )
    r2 = await worktree_mod.add_worktree(
        bare_root=bare_root,
        project_slug=_SLUG,
        worktree_path=wt2,
        branch="feature/x",
    )
    assert r1.ok and r2.ok
    bare_path = worktree_mod.bare_dir(bare_root, _SLUG)
    g1 = (Path(wt1) / ".git").read_text()
    g2 = (Path(wt2) / ".git").read_text()
    # Both worktrees reference the *same* bare via absolute path —
    # the layout guarantee the container mount relies on.
    assert f"gitdir: {bare_path}/worktrees/" in g1
    assert f"gitdir: {bare_path}/worktrees/" in g2


async def test_remove_worktree_cleans_dir_and_metadata(
    bare_root: str, worktree_root: str, fake_remote: str
) -> None:
    await worktree_mod.ensure_bare(
        bare_root=bare_root, project_slug=_SLUG, git_remote_url=fake_remote
    )
    wt = os.path.join(worktree_root, _SLUG, "ws-rm")
    await worktree_mod.add_worktree(
        bare_root=bare_root,
        project_slug=_SLUG,
        worktree_path=wt,
        branch="feature/x",
    )
    assert os.path.isdir(wt)

    res = await worktree_mod.remove_worktree(
        bare_root=bare_root, project_slug=_SLUG, worktree_path=wt
    )
    assert res.ok, res.stderr
    assert not os.path.exists(wt)
    meta = os.path.join(worktree_mod.bare_dir(bare_root, _SLUG), "worktrees")
    if os.path.isdir(meta):
        assert "ws-rm" not in os.listdir(meta)


async def test_remove_worktree_handles_missing_dir(
    bare_root: str, worktree_root: str
) -> None:
    """Idempotent — removing a worktree path that never existed must
    not error. We hit this when a previous create failed before the
    worktree was materialised."""
    res = await worktree_mod.remove_worktree(
        bare_root=bare_root,
        project_slug=_SLUG,
        worktree_path=os.path.join(worktree_root, _SLUG, "never-existed"),
    )
    assert res.ok


async def test_remove_worktree_handles_legacy_clone_layout(
    bare_root: str, worktree_root: str, fake_remote: str
) -> None:
    """Legacy workspaces are plain clones (full .git directory) and
    have no link to a bare. The remove path must still wipe them
    cleanly via rmtree."""
    proj_dir = os.path.join(worktree_root, _SLUG)
    Path(proj_dir).mkdir(parents=True, exist_ok=True)
    wt = os.path.join(proj_dir, "ws-legacy")
    subprocess.run(
        ["git", "clone", "--depth=1", fake_remote, wt],
        check=True,
        capture_output=True,
    )
    assert os.path.isdir(os.path.join(wt, ".git"))
    assert not worktree_mod.is_bare_initialized(bare_root, _SLUG)

    res = await worktree_mod.remove_worktree(
        bare_root=bare_root, project_slug=_SLUG, worktree_path=wt
    )
    assert res.ok
    assert not os.path.exists(wt)


# ─────────────────────────────────── migration helpers ──


def test_read_worktree_gitdir(tmp_path: Path) -> None:
    wt = tmp_path / "ws"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /var/lib/gapt-bare/proj/worktrees/01K\n")
    assert (
        worktree_mod.read_worktree_gitdir(str(wt))
        == "/var/lib/gapt-bare/proj/worktrees/01K"
    )


def test_derive_bare_from_gitdir() -> None:
    assert (
        worktree_mod.derive_bare_from_gitdir(
            "/var/lib/gapt-bare/proj/worktrees/01K"
        )
        == "/var/lib/gapt-bare/proj"
    )
    # Trailing slash tolerated.
    assert (
        worktree_mod.derive_bare_from_gitdir(
            "/var/lib/gapt-bare/proj/worktrees/01K/"
        )
        == "/var/lib/gapt-bare/proj"
    )
    # Garbage path returns None.
    assert worktree_mod.derive_bare_from_gitdir("/something/else") is None


def test_rewrite_worktree_gitdir(tmp_path: Path) -> None:
    wt = tmp_path / "ws"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /old/path\n")
    worktree_mod.rewrite_worktree_gitdir(str(wt), "/new/bare/worktrees/01K")
    assert (wt / ".git").read_text() == "gitdir: /new/bare/worktrees/01K\n"
