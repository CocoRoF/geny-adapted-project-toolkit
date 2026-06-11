"""Bare-repo migration (legacy `<project>/.bare/` → new
`<bare_root>/<slug>/`) — 2026-05-29.

We exercise the sync `migrate_project_bare` directly (the DB-driven
`run_bare_migration` is one thin layer above and is covered by an
import-level smoke at the bottom). Uses a real `git` binary against
a local fake remote so the metadata files have the right shape;
the migration walks them by content."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from gapt_server.domains.workspaces import worktree as worktree_mod
from gapt_server.domains.workspaces.bare_migration import (
    migrate_project_bare,
    run_bare_migration,
)


def _git(*args: str, cwd: str) -> None:
    # safe.bareRepository=all mirrors what production `_run_git`
    # injects — hosts hardened with `safe.bareRepository=explicit`
    # refuse worktree ops against the fixture's bare clone otherwise.
    subprocess.run(
        ["git", "-c", "safe.bareRepository=all", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def legacy_project(tmp_path: Path) -> tuple[str, str, str]:
    """Mimic the pre-2026-05-29 layout: `<workspace_root>/<slug>/`
    contains `.bare/` *plus* one worktree. The worktree's `.git` file
    references the bare via absolute path. Returns
    `(workspace_root, project_slug, worktree_path)`."""
    workspace_root = tmp_path / "workspace"
    slug = "demo"
    project_root = workspace_root / slug
    project_root.mkdir(parents=True)
    # Seed a tiny upstream so `git clone --bare` + `worktree add` work.
    src = tmp_path / "src"
    src.mkdir()
    _git("init", "--initial-branch=main", cwd=str(src))
    _git("config", "user.email", "t@e", cwd=str(src))
    _git("config", "user.name", "t", cwd=str(src))
    (src / "README.md").write_text("hi\n")
    _git("add", "README.md", cwd=str(src))
    _git("commit", "-m", "init", cwd=str(src))
    # Old layout: bare lives at <project_root>/.bare
    legacy_bare = project_root / ".bare"
    subprocess.run(
        [
            "git",
            "-c",
            "safe.bareRepository=all",
            "clone",
            "--bare",
            str(src),
            str(legacy_bare),
        ],
        check=True,
        capture_output=True,
    )
    # And one worktree alongside.
    wid = "01KSPTCV36C23NZ2P0Y8RMFH4K"
    wt = project_root / wid
    subprocess.run(
        [
            "git",
            "-c",
            "safe.bareRepository=all",
            "-C",
            str(legacy_bare),
            "worktree",
            "add",
            str(wt),
            "main",
        ],
        check=True,
        capture_output=True,
    )
    # Verify pre-condition: `.git` points at the legacy absolute path.
    legacy_gitdir = (wt / ".git").read_text()
    assert str(legacy_bare) in legacy_gitdir, legacy_gitdir
    return str(workspace_root), slug, str(wt)


# ────────────────────────────── migrate_project_bare ──


def test_migrate_moves_bare_and_rewrites_gitdir(
    legacy_project: tuple[str, str, str], tmp_path: Path
) -> None:
    workspace_root, slug, wt = legacy_project
    new_bare_root = str(tmp_path / "gapt-bare")

    verdict = migrate_project_bare(
        workspace_root=workspace_root,
        workspace_bare_root=new_bare_root,
        project_slug=slug,
    )
    assert verdict == "migrated"

    # Old location empty, new location populated.
    assert not os.path.exists(os.path.join(workspace_root, slug, ".bare"))
    new_bare = worktree_mod.bare_dir(new_bare_root, slug)
    assert os.path.isfile(os.path.join(new_bare, "HEAD"))

    # Worktree's `.git` rewritten to the new absolute path.
    gitdir = (Path(wt) / ".git").read_text()
    expected = (
        f"gitdir: {new_bare}/worktrees/{os.path.basename(wt)}"
    )
    assert gitdir.strip() == expected


def test_migrate_returns_no_legacy_when_already_new_layout(
    tmp_path: Path,
) -> None:
    """Idempotent: a second run after a successful migration must be
    a no-op, returning the `"no_legacy"` verdict."""
    workspace_root = str(tmp_path / "workspace")
    bare_root = str(tmp_path / "gapt-bare")
    os.makedirs(os.path.join(workspace_root, "demo"))
    verdict = migrate_project_bare(
        workspace_root=workspace_root,
        workspace_bare_root=bare_root,
        project_slug="demo",
    )
    assert verdict == "no_legacy"


def test_migrate_refuses_when_target_already_exists(
    legacy_project: tuple[str, str, str], tmp_path: Path
) -> None:
    """If both old + new bare exist (interrupted previous run / manual
    fiddling), refuse to overwrite — surface as `"target_exists"` so
    the operator can investigate."""
    workspace_root, slug, _wt = legacy_project
    new_bare_root = str(tmp_path / "gapt-bare")
    os.makedirs(worktree_mod.bare_dir(new_bare_root, slug))
    verdict = migrate_project_bare(
        workspace_root=workspace_root,
        workspace_bare_root=new_bare_root,
        project_slug=slug,
    )
    assert verdict == "target_exists"
    # Legacy bare untouched.
    assert os.path.isdir(os.path.join(workspace_root, slug, ".bare"))


def test_migrate_post_state_keeps_git_working(
    legacy_project: tuple[str, str, str], tmp_path: Path
) -> None:
    """After migration, running `git status` from the worktree must
    succeed — proves the rewritten `.git` actually resolves end-to-end
    (not just that the file content looks right). This is the contract
    the workspace container will rely on."""
    workspace_root, slug, wt = legacy_project
    new_bare_root = str(tmp_path / "gapt-bare")
    migrate_project_bare(
        workspace_root=workspace_root,
        workspace_bare_root=new_bare_root,
        project_slug=slug,
    )
    res = subprocess.run(
        ["git", "status"], cwd=wt, capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr


# ─────────────────────────────── run_bare_migration ──


@pytest.mark.asyncio
async def test_run_bare_migration_smoke() -> None:
    """`run_bare_migration` is one DB call + per-project loop. The
    important pieces (verdicts, FS effects) are covered in the sync
    tests above. This smoke is just to ensure the async function
    imports + runs against an empty session_factory without exploding."""
    from contextlib import asynccontextmanager
    from dataclasses import dataclass

    @dataclass
    class _Row:
        slug: str
        archived_at: object | None = None

        def __iter__(self):
            yield self.slug
            yield self.archived_at

    class _Result:
        def all(self):
            return []

    class _Session:
        async def execute(self, _stmt):
            return _Result()

    @asynccontextmanager
    async def _factory():
        yield _Session()

    report = await run_bare_migration(
        session_factory=_factory,
        workspace_root="/tmp/x",
        workspace_bare_root="/tmp/y",
    )
    assert report.migrated == []
    assert report.failed == []
