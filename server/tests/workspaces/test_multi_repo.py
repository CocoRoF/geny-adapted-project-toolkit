"""Workspace creation — Phase N.4 multi-repo orchestration.

The Phase N.4 changes route the clone path through the project's
``ProjectRepository`` rows instead of the single ``Project.git_remote_url``.
Three shapes have to keep working without confusing the existing
single-repo callers:

* **Empty** — no Repository rows. Workspace comes up with just a
  worktree dir; the file tree shows whatever the operator drops in
  there manually (or what ``git init`` produces later).
* **Legacy single-repo** — one Repository row at ``subpath=''``.
  Backwards-compatible with every pre-N.4 workspace: the bare repo
  still lives at ``<bare_root>/<project_slug>/`` (NO repo-id segment)
  so already-cloned workspaces' ``.git`` pointers keep resolving.
* **Multi-repo** — two or more Repository rows, each with its own
  subpath. Each repo gets its own bare at
  ``<bare_root>/<project_slug>/<repo_id>/`` and lands at
  ``<worktree>/<subpath>/`` so VS-Code-style "open this workspace"
  shows them as side-by-side root folders.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from gapt_server.domains.workspaces.service import (
    RepoSpec,
    WorkspaceService,
    _bare_slug_for,
    _worktree_subdir_for,
)


def test_bare_slug_legacy_keeps_old_shape() -> None:
    """Legacy single-repo (subpath='') must NOT nest under
    ``<repo_id>`` — that would orphan every pre-N.4 workspace's
    bare. The slug we hand to ``ensure_bare`` stays equal to the
    plain project slug."""
    repo = RepoSpec(
        repo_id="01KREPOABCDEFGHJKMNPQRSTVWX",
        subpath="",
        git_remote_url="https://example.com/demo.git",
        branch="main",
    )
    assert _bare_slug_for("demo", repo, legacy_layout=True) == "demo"


def test_bare_slug_multi_repo_nests_under_repo_id() -> None:
    """Multi-repo: each repo's bare lives at
    ``<bare_root>/<project_slug>/<repo_id>/`` so two repos with
    different remote URLs don't clobber each other on disk."""
    repo = RepoSpec(
        repo_id="01KREPOABCDEFGHJKMNPQRSTVWX",
        subpath="frontend",
        git_remote_url="https://example.com/frontend.git",
        branch="main",
    )
    assert (
        _bare_slug_for("demo", repo, legacy_layout=False)
        == "demo/01KREPOABCDEFGHJKMNPQRSTVWX"
    )


def test_worktree_subdir_root_for_empty_subpath() -> None:
    """``subpath=''`` means "this repo IS the worktree root" — the
    legacy single-repo layout. The returned dir equals the
    workspace's worktree dir verbatim, with no trailing segment."""
    repo = RepoSpec(
        repo_id="rid",
        subpath="",
        git_remote_url="https://example.com/x.git",
        branch="main",
    )
    assert _worktree_subdir_for("/workspace/demo/01ws", repo) == "/workspace/demo/01ws"


def test_worktree_subdir_nests_under_subpath() -> None:
    repo = RepoSpec(
        repo_id="rid",
        subpath="frontend",
        git_remote_url="https://example.com/x.git",
        branch="main",
    )
    assert (
        _worktree_subdir_for("/workspace/demo/01ws", repo)
        == "/workspace/demo/01ws/frontend"
    )


# ────────────────────────────────────────────── live orchestration ──


@pytest.fixture
def tmp_worktree(tmp_path) -> str:
    """A temp dir that DOESN'T exist yet — the orchestrator must
    create it. Tests for the resume case manually pre-create
    subpaths."""
    return str(tmp_path / "wt")


def _make_service(monkeypatch, tmp_bare_root: str) -> WorkspaceService:
    """Build a WorkspaceService with the bare/add stubs the route
    tests use — neutralises the real-git work so we exercise only
    the orchestrator's branching logic."""
    from gapt_server.domains.workspaces import service as ws_service
    from gapt_server.domains.workspaces import worktree as worktree_mod

    seen_bare: list[tuple[str, str]] = []
    seen_add: list[tuple[str, str, str]] = []

    async def _ok_ensure_bare(
        *,
        bare_root: str = "",
        project_slug: str = "",
        git_remote_url: str = "",
        extra_config: list[str] | None = None,
    ) -> worktree_mod.GitRunResult:
        seen_bare.append((project_slug, git_remote_url))
        Path(os.path.join(bare_root, project_slug)).mkdir(parents=True, exist_ok=True)
        return worktree_mod.GitRunResult(0, "stub bare", "")

    async def _ok_add_worktree(
        *,
        bare_root: str = "",
        project_slug: str = "",
        worktree_path: str,
        branch: str,
    ) -> worktree_mod.GitRunResult:
        seen_add.append((project_slug, worktree_path, branch))
        # Mirror real `git worktree add`: creates the dir + a `.git`
        # marker so the orchestrator's resume check doesn't mistake
        # a successful clone for a missing one on re-runs.
        Path(worktree_path).mkdir(parents=True, exist_ok=True)
        Path(os.path.join(worktree_path, ".git")).write_text("gitdir: stub\n")
        return worktree_mod.GitRunResult(0, "stub add", "")

    monkeypatch.setattr(worktree_mod, "ensure_bare", _ok_ensure_bare)
    monkeypatch.setattr(worktree_mod, "add_worktree", _ok_add_worktree)

    # Minimal viable WorkspaceService — we only call
    # `_prepare_worktree_multi`, which doesn't touch the sandbox
    # backend or audit sink. A bare-bones fake satisfies the
    # required kwargs.
    class _FakeSandbox:
        async def create(self, *_a, **_kw): ...
        async def start(self, *_a, **_kw): ...
        async def stop(self, *_a, **_kw): ...
        async def destroy(self, *_a, **_kw): ...

    svc = WorkspaceService(
        sandbox_backend=_FakeSandbox(),  # type: ignore[arg-type]
        sandbox_image="ghcr.io/gapt/test:dev",
        workspace_bare_root=tmp_bare_root,
    )
    # Expose the spy lists on the service for assertion.
    svc._spy_bare = seen_bare  # type: ignore[attr-defined]
    svc._spy_add = seen_add  # type: ignore[attr-defined]
    return svc


@pytest.mark.asyncio
async def test_orchestrator_empty_project_creates_worktree_only(
    monkeypatch, tmp_path, tmp_worktree
) -> None:
    """Empty project (no Repository rows) → outcome=empty, the
    worktree dir exists, NO bare / NO add_worktree call ran."""
    svc = _make_service(monkeypatch, str(tmp_path / "bare"))

    outcome, detail = await svc._prepare_worktree_multi(
        worktree=tmp_worktree,
        project_slug="demo",
        repos=[],
        credentials={},
    )
    assert outcome == "empty", detail
    assert os.path.isdir(tmp_worktree)
    assert svc._spy_bare == []  # type: ignore[attr-defined]
    assert svc._spy_add == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_orchestrator_legacy_single_repo_preserves_bare_shape(
    monkeypatch, tmp_path, tmp_worktree
) -> None:
    """One repo with subpath='' → the bare slug passed to ensure_bare
    is the PLAIN project slug, NOT nested under <repo_id>. This is
    what keeps pre-N.4 workspaces' existing bares valid."""
    svc = _make_service(monkeypatch, str(tmp_path / "bare"))

    repos = [
        RepoSpec(
            repo_id="01KLEGACYREPOIDABCDEFGHJK",
            subpath="",
            git_remote_url="https://example.com/demo.git",
            branch="main",
        )
    ]
    outcome, detail = await svc._prepare_worktree_multi(
        worktree=tmp_worktree,
        project_slug="demo",
        repos=repos,
        credentials={},
    )
    assert outcome == "cloned", detail
    # No repo-id segment in the slug.
    assert svc._spy_bare == [("demo", "https://example.com/demo.git")]  # type: ignore[attr-defined]
    # Worktree path is the workspace root (no subdir).
    assert svc._spy_add[0][1] == tmp_worktree  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_orchestrator_multi_repo_clones_each_into_its_subdir(
    monkeypatch, tmp_path, tmp_worktree
) -> None:
    """Two repos with non-empty subpaths land side-by-side under the
    workspace's worktree. Each gets its own bare under
    ``<bare_root>/<project_slug>/<repo_id>/``."""
    svc = _make_service(monkeypatch, str(tmp_path / "bare"))

    repos = [
        RepoSpec(
            repo_id="01KREPOFRONTEND0000000000",
            subpath="frontend",
            git_remote_url="https://example.com/front.git",
            branch="main",
        ),
        RepoSpec(
            repo_id="01KREPOBACKEND00000000000",
            subpath="backend",
            git_remote_url="https://example.com/back.git",
            branch="develop",
        ),
    ]
    outcome, _ = await svc._prepare_worktree_multi(
        worktree=tmp_worktree,
        project_slug="demo",
        repos=repos,
        credentials={},
    )
    assert outcome == "cloned"
    # Each bare nested under <demo>/<repo_id>.
    bare_slugs = sorted(s for s, _ in svc._spy_bare)  # type: ignore[attr-defined]
    assert bare_slugs == [
        "demo/01KREPOBACKEND00000000000",
        "demo/01KREPOFRONTEND0000000000",
    ]
    # Each worktree at <worktree>/<subpath>.
    add_paths = sorted(p for _, p, _ in svc._spy_add)  # type: ignore[attr-defined]
    assert add_paths == [
        os.path.join(tmp_worktree, "backend"),
        os.path.join(tmp_worktree, "frontend"),
    ]


@pytest.mark.asyncio
async def test_orchestrator_resumes_partial_state(
    monkeypatch, tmp_path, tmp_worktree
) -> None:
    """If a previous create crashed mid-way and one subdir already
    has a ``.git`` marker, the resume pass must NOT re-clone it.
    Only the missing repo's bare+worktree calls fire."""
    svc = _make_service(monkeypatch, str(tmp_path / "bare"))

    # Pre-create the frontend subdir as if it had been cloned.
    frontend_dir = os.path.join(tmp_worktree, "frontend")
    Path(frontend_dir).mkdir(parents=True)
    Path(os.path.join(frontend_dir, ".git")).write_text("gitdir: prior\n")

    repos = [
        RepoSpec(
            repo_id="01KREPOFRONTEND0000000000",
            subpath="frontend",
            git_remote_url="https://example.com/front.git",
            branch="main",
        ),
        RepoSpec(
            repo_id="01KREPOBACKEND00000000000",
            subpath="backend",
            git_remote_url="https://example.com/back.git",
            branch="main",
        ),
    ]
    outcome, _ = await svc._prepare_worktree_multi(
        worktree=tmp_worktree,
        project_slug="demo",
        repos=repos,
        credentials={},
    )
    assert outcome == "cloned"
    # ONLY backend triggered bare/add — frontend was skipped.
    assert svc._spy_bare == [  # type: ignore[attr-defined]
        ("demo/01KREPOBACKEND00000000000", "https://example.com/back.git")
    ]
    assert svc._spy_add == [  # type: ignore[attr-defined]
        ("demo/01KREPOBACKEND00000000000", os.path.join(tmp_worktree, "backend"), "main")
    ]


@pytest.mark.asyncio
async def test_orchestrator_empty_remote_just_mkdirs_the_subdir(
    monkeypatch, tmp_path, tmp_worktree
) -> None:
    """A Repository row with NULL git_remote_url (reserved for the
    future "subdir without git" case) — orchestrator just makes the
    subdir, doesn't clone."""
    svc = _make_service(monkeypatch, str(tmp_path / "bare"))

    repos = [
        RepoSpec(
            repo_id="01KEMPTYSCRATCHPADAAAA",
            subpath="scratch",
            git_remote_url=None,
            branch="main",
        ),
    ]
    outcome, _ = await svc._prepare_worktree_multi(
        worktree=tmp_worktree,
        project_slug="demo",
        repos=repos,
        credentials={},
    )
    assert outcome == "cloned"
    assert os.path.isdir(os.path.join(tmp_worktree, "scratch"))
    assert svc._spy_bare == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_orchestrator_partial_outcome_when_one_repo_fails(
    monkeypatch, tmp_path, tmp_worktree
) -> None:
    """One repo's clone failing must NOT block the others — the
    orchestrator carries on, returns ``("partial", <detail>)`` so
    the UI can flag the broken subpath without losing the working
    ones."""
    from gapt_server.domains.workspaces import worktree as worktree_mod

    svc = _make_service(monkeypatch, str(tmp_path / "bare"))

    # Patch ensure_bare to fail for one specific URL.
    real_ensure_bare = worktree_mod.ensure_bare

    async def _picky_ensure_bare(
        *,
        bare_root: str = "",
        project_slug: str = "",
        git_remote_url: str = "",
        extra_config: list[str] | None = None,
    ) -> worktree_mod.GitRunResult:
        if "broken" in git_remote_url:
            return worktree_mod.GitRunResult(128, "", "fatal: bad repo")
        return await real_ensure_bare(
            bare_root=bare_root,
            project_slug=project_slug,
            git_remote_url=git_remote_url,
            extra_config=extra_config,
        )

    monkeypatch.setattr(worktree_mod, "ensure_bare", _picky_ensure_bare)

    repos = [
        RepoSpec(
            repo_id="01KOK0000000000000000000",
            subpath="ok",
            git_remote_url="https://example.com/ok.git",
            branch="main",
        ),
        RepoSpec(
            repo_id="01KBROKEN000000000000000",
            subpath="broken",
            git_remote_url="https://example.com/broken.git",
            branch="main",
        ),
    ]
    outcome, detail = await svc._prepare_worktree_multi(
        worktree=tmp_worktree,
        project_slug="demo",
        repos=repos,
        credentials={},
    )
    assert outcome == "partial", detail
    assert "broken" in detail
    # The OK repo was still cloned successfully.
    assert os.path.exists(os.path.join(tmp_worktree, "ok", ".git"))
