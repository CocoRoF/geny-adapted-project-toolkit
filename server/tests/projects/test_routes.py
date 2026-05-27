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
