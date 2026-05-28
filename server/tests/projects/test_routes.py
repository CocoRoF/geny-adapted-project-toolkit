"""HTTP-level tests for /_gapt/api/projects + environments."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.settings import Settings
from tests._helpers.db_guard import assert_safe_to_reset

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

SERVER_ROOT = Path(__file__).resolve().parents[2]


def _require_dsn() -> str:
    dsn = os.environ.get("GAPT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("GAPT_TEST_POSTGRES_DSN unset")
    return dsn


def _reset_and_upgrade(sync_dsn: str) -> None:
    assert_safe_to_reset(sync_dsn)
    with psycopg.connect(sync_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    env = os.environ.copy()
    env["GAPT_POSTGRES_DSN"] = sync_dsn
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=SERVER_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )


@dataclass
class _ProjFixture:
    app: FastAPI
    audit: InMemoryAuditSink


@pytest_asyncio.fixture
async def proj_fx() -> AsyncIterator[_ProjFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    # auth_enabled=False makes every request auto-authenticate as the
    # admin; tests don't need to exercise the cookie dance.
    settings = Settings(postgres_dsn=sync_dsn, auth_enabled=False)  # type: ignore[arg-type]
    audit = InMemoryAuditSink()
    container = build_container(settings, audit_sink=audit)
    app = create_app(settings=settings, container=container)
    try:
        yield _ProjFixture(app=app, audit=audit)
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_project_lifecycle(proj_fx: _ProjFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=proj_fx.app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/_gapt/api/projects",
            json={
                "slug": "demo",
                "display_name": "Demo Project",
                "git_remote_url": "https://github.com/CocoRoF/demo.git",
                "git_provider": "github",
                "default_compose_paths": ["compose.dev.yml"],
            },
        )
        assert created.status_code == 201, created.text
        proj = created.json()
        assert proj["slug"] == "demo"
        assert proj["archived_at"] is None
        project_id = proj["id"]

        listing = await client.get("/_gapt/api/projects")
        assert listing.status_code == 200
        assert any(p["id"] == project_id for p in listing.json())

        single = await client.get(f"/_gapt/api/projects/{project_id}")
        assert single.status_code == 200
        assert single.json()["display_name"] == "Demo Project"

        patched = await client.patch(
            f"/_gapt/api/projects/{project_id}",
            json={"display_name": "Demo (renamed)"},
        )
        assert patched.status_code == 200
        assert patched.json()["display_name"] == "Demo (renamed)"

        archived = await client.delete(f"/_gapt/api/projects/{project_id}")
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

        # Default list filters out archived rows.
        post_archive = await client.get("/_gapt/api/projects")
        assert all(p["id"] != project_id for p in post_archive.json())
        with_archived = await client.get("/_gapt/api/projects?include_archived=true")
        assert any(p["id"] == project_id for p in with_archived.json())

    # Audit: create + update + archive events all recorded.
    actions = [e.action for e in proj_fx.audit.events]
    assert "project.create" in actions
    assert "project.update" in actions
    assert "project.archive" in actions


@pytest.mark.asyncio
async def test_duplicate_slug_409(proj_fx: _ProjFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=proj_fx.app), base_url="http://test"
    ) as client:
        body = {
            "slug": "twin",
            "display_name": "Twin",
            "git_remote_url": "https://example.com/twin.git",
        }
        first = await client.post("/_gapt/api/projects", json=body)
        assert first.status_code == 201
        second = await client.post("/_gapt/api/projects", json=body)
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "project.slug_taken"


@pytest.mark.asyncio
async def test_environment_crud(proj_fx: _ProjFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=proj_fx.app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/_gapt/api/projects",
            json={
                "slug": "envtest",
                "display_name": "Env Test",
                "git_remote_url": "https://example.com/envtest.git",
            },
        )
        project_id = created.json()["id"]

        env = await client.post(
            f"/_gapt/api/projects/{project_id}/environments",
            json={
                "name": "dev",
                "deploy_target_kind": "local",
                "deploy_target_config": {"port": 3000},
            },
        )
        assert env.status_code == 201, env.text
        assert env.json()["name"] == "dev"

        listing = await client.get(f"/_gapt/api/projects/{project_id}/environments")
        assert listing.status_code == 200
        assert [e["name"] for e in listing.json()] == ["dev"]

        # Duplicate name = 409.
        dup = await client.post(
            f"/_gapt/api/projects/{project_id}/environments",
            json={"name": "dev", "deploy_target_kind": "local"},
        )
        assert dup.status_code == 409
        assert dup.json()["detail"]["code"] == "environment.name_taken"


@pytest.mark.asyncio
async def test_environment_target_config_validation(
    proj_fx: _ProjFixture,
) -> None:
    """Phase H.1 — POST `/environments` with a malformed
    `deploy_target_config` must return 422 with a field-level error
    list so the EnvironmentEditor can red-line the exact knob."""
    async with AsyncClient(
        transport=ASGITransport(app=proj_fx.app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/_gapt/api/projects",
            json={
                "slug": "h1test",
                "display_name": "H.1 test",
                "git_remote_url": "https://example.com/h1.git",
            },
        )
        project_id = created.json()["id"]

        # Bad port → 422 with the offending field surfaced.
        bad = await client.post(
            f"/_gapt/api/projects/{project_id}/environments",
            json={
                "name": "broken",
                "deploy_target_kind": "local",
                "deploy_target_config": {"primary_port": 99999},
            },
        )
        assert bad.status_code == 422, bad.text
        detail = bad.json()["detail"]
        assert detail["code"] == "environment.target_config_invalid"
        assert any(f["loc"] == ["primary_port"] for f in detail["fields"])

        # K8s kind → distinct 422 code (UI can banner this).
        k8s = await client.post(
            f"/_gapt/api/projects/{project_id}/environments",
            json={
                "name": "in-cluster",
                "deploy_target_kind": "k8s",
                "deploy_target_config": {},
            },
        )
        assert k8s.status_code == 422, k8s.text
        assert (
            k8s.json()["detail"]["code"]
            == "environment.target_kind_not_supported"
        )

        # Valid config → 201, even when sparse.
        ok = await client.post(
            f"/_gapt/api/projects/{project_id}/environments",
            json={
                "name": "prod",
                "deploy_target_kind": "local",
                "deploy_target_config": {
                    "compose_path": "docker-compose.yml",
                    "primary_port": 3000,
                },
            },
        )
        assert ok.status_code == 201, ok.text
        cfg = ok.json()["deploy_target_config"]
        # Round-trip cleaned dict — Nones dropped, valid fields kept.
        assert cfg["compose_path"] == "docker-compose.yml"
        assert cfg["primary_port"] == 3000


@pytest.mark.asyncio
async def test_remote_branches_endpoint(
    proj_fx: _ProjFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: the modal asks for branches → 200 with parsed
    heads. Then ls-remote fails → 502 with the reason surfaced so
    the frontend can fall back to free-text."""
    from gapt_server.domains import git_remote

    git_remote._cache.clear()

    async with AsyncClient(
        transport=ASGITransport(app=proj_fx.app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/_gapt/api/projects",
            json={
                "slug": "branchtest",
                "display_name": "Branch test",
                "git_remote_url": "https://github.com/example/branchtest.git",
            },
        )
        project_id = created.json()["id"]

        # Stub the ls-remote call so we don't actually hit the network.
        captured: dict[str, object] = {}

        async def _stub_list(
            *, project_id: str, git_remote_url: str, github_token: str | None
        ) -> git_remote.RemoteBranches:
            captured["project_id"] = project_id
            captured["git_remote_url"] = git_remote_url
            captured["github_token"] = github_token
            return git_remote.RemoteBranches(
                head="main", branches=["main", "develop"], cached_at=0.0
            )

        monkeypatch.setattr(
            "gapt_server.routers.projects.list_remote_branches", _stub_list
        )

        ok = await client.get(f"/_gapt/api/projects/{project_id}/remote-branches")
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body == {"head": "main", "branches": ["main", "develop"]}
        # The project has no `git_auth_secret_ref`, so the token must
        # come from the host fallback (or be None if the test env has
        # no GH_TOKEN). What the endpoint must NOT do is silently lose
        # the URL on the way to ls-remote — that's the wiring under test.
        assert captured["git_remote_url"] == "https://github.com/example/branchtest.git"

        # ls-remote failure → 502 with the reason exposed to the UI.
        async def _stub_fail(**_kwargs: object) -> None:
            raise git_remote.RemoteBranchesError(
                "fatal: Authentication failed for 'https://…'"
            )

        monkeypatch.setattr(
            "gapt_server.routers.projects.list_remote_branches", _stub_fail
        )

        bad = await client.get(f"/_gapt/api/projects/{project_id}/remote-branches")
        assert bad.status_code == 502, bad.text
        assert bad.json()["detail"]["code"] == "git.ls_remote_failed"
        assert "Authentication failed" in bad.json()["detail"]["reason"]

        # Unknown project = 404 (sanity — guards against IDOR if a
        # future change makes the endpoint less strict).
        nope = await client.get("/_gapt/api/projects/does-not-exist/remote-branches")
        assert nope.status_code == 404
