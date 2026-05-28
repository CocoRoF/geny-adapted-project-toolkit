"""Unit tests for the Phase C.1 bare-repo + worktree primitives.

Uses a real `git` binary against a local fake remote (no network)
so the tests exercise the actual command surface, not a mock. The
fake remote is a fresh bare repo seeded with one commit; tests
create worktrees off it, mutate, and assert disk layout.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from gapt_server.domains.workspaces import worktree as worktree_mod


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
    # Bare clone so the path looks like a real remote.
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(src), str(bare)],
        check=True,
        capture_output=True,
    )
    return str(bare)


async def test_ensure_bare_creates_directory(
    tmp_path: Path, fake_remote: str
) -> None:
    project_root = str(tmp_path / "proj")
    res = await worktree_mod.ensure_bare(project_root, git_remote_url=fake_remote)
    assert res.ok, res.stderr
    assert worktree_mod.is_bare_initialized(project_root)
    assert os.path.isfile(os.path.join(project_root, ".bare", "HEAD"))


async def test_ensure_bare_idempotent_runs_fetch(
    tmp_path: Path, fake_remote: str
) -> None:
    """Second call must not re-clone — it should fetch updates so the
    operation is cheap and refreshes refs."""
    project_root = str(tmp_path / "proj")
    first = await worktree_mod.ensure_bare(project_root, git_remote_url=fake_remote)
    assert first.ok
    # Touch the bare's mtime to verify the second call doesn't blow it
    # away (clone --bare would).
    bare_head = os.path.join(project_root, ".bare", "HEAD")
    mtime_before = os.path.getmtime(bare_head)
    second = await worktree_mod.ensure_bare(project_root, git_remote_url=fake_remote)
    assert second.ok, second.stderr
    # File still there — wasn't replaced by a fresh clone.
    assert os.path.getmtime(bare_head) == mtime_before


async def test_add_worktree_for_existing_branch(
    tmp_path: Path, fake_remote: str
) -> None:
    project_root = str(tmp_path / "proj")
    await worktree_mod.ensure_bare(project_root, git_remote_url=fake_remote)
    wt = str(tmp_path / "proj" / "ws-001")
    res = await worktree_mod.add_worktree(
        project_root, worktree_path=wt, branch="feature/x"
    )
    assert res.ok, res.stderr
    # The branch's file lands in the worktree.
    assert (Path(wt) / "feature.txt").read_text() == "on feature\n"
    # And it's a real git worktree (the .git is a file pointer).
    assert worktree_mod.is_worktree_path(wt)


async def test_add_worktree_for_new_branch(
    tmp_path: Path, fake_remote: str
) -> None:
    """A branch that doesn't exist on origin should be created from
    HEAD — used when the operator wants to start a feature on a fresh
    branch from inside GAPT."""
    project_root = str(tmp_path / "proj")
    await worktree_mod.ensure_bare(project_root, git_remote_url=fake_remote)
    wt = str(tmp_path / "proj" / "ws-002")
    res = await worktree_mod.add_worktree(
        project_root, worktree_path=wt, branch="feature/brand-new"
    )
    assert res.ok, res.stderr
    # Should have started from HEAD (main), so README.md exists but
    # feature.txt does NOT.
    assert (Path(wt) / "README.md").read_text() == "hello main\n"
    assert not (Path(wt) / "feature.txt").exists()


async def test_two_worktrees_same_bare_share_objects(
    tmp_path: Path, fake_remote: str
) -> None:
    """The whole point: two workspaces on the same project must share
    one .bare and only cost their working trees."""
    project_root = str(tmp_path / "proj")
    await worktree_mod.ensure_bare(project_root, git_remote_url=fake_remote)
    wt1 = str(tmp_path / "proj" / "ws-A")
    wt2 = str(tmp_path / "proj" / "ws-B")
    r1 = await worktree_mod.add_worktree(
        project_root, worktree_path=wt1, branch="main"
    )
    r2 = await worktree_mod.add_worktree(
        project_root, worktree_path=wt2, branch="feature/x"
    )
    assert r1.ok and r2.ok
    # Each worktree's .git file should point back to the SAME bare.
    g1 = (Path(wt1) / ".git").read_text()
    g2 = (Path(wt2) / ".git").read_text()
    assert ".bare/worktrees/" in g1
    assert ".bare/worktrees/" in g2


async def test_remove_worktree_cleans_dir_and_metadata(
    tmp_path: Path, fake_remote: str
) -> None:
    project_root = str(tmp_path / "proj")
    await worktree_mod.ensure_bare(project_root, git_remote_url=fake_remote)
    wt = str(tmp_path / "proj" / "ws-rm")
    await worktree_mod.add_worktree(
        project_root, worktree_path=wt, branch="feature/x"
    )
    assert os.path.isdir(wt)

    res = await worktree_mod.remove_worktree(project_root, worktree_path=wt)
    assert res.ok, res.stderr
    assert not os.path.exists(wt)
    # The bare's worktrees metadata directory should not list the
    # removed worktree.
    meta = os.path.join(project_root, ".bare", "worktrees")
    if os.path.isdir(meta):
        assert "ws-rm" not in os.listdir(meta)


async def test_remove_worktree_handles_missing_dir(
    tmp_path: Path,
) -> None:
    """Idempotent — removing a worktree path that never existed must
    not error. We hit this when a previous create failed before the
    worktree was materialised."""
    project_root = str(tmp_path / "proj")
    res = await worktree_mod.remove_worktree(
        project_root, worktree_path=str(tmp_path / "proj" / "never-existed")
    )
    assert res.ok


async def test_remove_worktree_handles_legacy_clone_layout(
    tmp_path: Path, fake_remote: str
) -> None:
    """Legacy workspaces are plain clones (full .git directory) and
    have no link to a .bare. The remove path must still wipe them
    cleanly via rmtree."""
    project_root = str(tmp_path / "proj")
    wt = str(tmp_path / "proj" / "ws-legacy")
    # Mimic the old code path: clone into the worktree dir directly.
    Path(project_root).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth=1", fake_remote, wt],
        check=True,
        capture_output=True,
    )
    assert os.path.isdir(os.path.join(wt, ".git"))  # full clone, not worktree
    assert not worktree_mod.is_bare_initialized(project_root)

    res = await worktree_mod.remove_worktree(project_root, worktree_path=wt)
    assert res.ok
    assert not os.path.exists(wt)
