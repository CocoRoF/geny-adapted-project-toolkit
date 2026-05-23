"""HTTP-level tests for /api/projects + environments."""

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
from sqlalchemy import select

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.db import models
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.auth.idp import build_memory_idp
from gapt_server.routers.auth import set_auth_idp
from gapt_server.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from gapt_server.domains.auth.idp import MagicLinkIdp

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
    idp: MagicLinkIdp
    audit: InMemoryAuditSink


@pytest_asyncio.fixture
async def proj_fx() -> AsyncIterator[_ProjFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn)  # type: ignore[arg-type]
    audit = InMemoryAuditSink()
    container = build_container(settings, audit_sink=audit)
    app = create_app(settings=settings, container=container)
    idp = build_memory_idp()
    set_auth_idp(idp)
    try:
        yield _ProjFixture(app=app, idp=idp, audit=audit)
    finally:
        await container.aclose()


async def _login_and_get_org(client: AsyncClient, idp: MagicLinkIdp, email: str) -> tuple[str, str]:
    """Returns (user_id, org_id) for the just-logged-in user (their default org)."""
    await client.post("/api/auth/magic-link", json={"email": email})
    token = next(iter(idp._tokens._items))
    cb = await client.get(f"/api/auth/magic-link/callback?token={token}")
    user_id = cb.json()["user_id"]

    # Read the default org id directly from the DB via the same session
    # factory the app uses.
    container = client._transport.app.state.container  # type: ignore[attr-defined]
    async with container.session_factory() as db:  # type: ignore[union-attr]
        row = (
            await db.execute(
                select(models.OrgMembership).where(models.OrgMembership.user_id == user_id)
            )
        ).scalar_one()
    return user_id, row.org_id


@pytest.mark.asyncio
async def test_project_lifecycle(proj_fx: _ProjFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=proj_fx.app), base_url="http://test"
    ) as client:
        _user_id, org_id = await _login_and_get_org(client, proj_fx.idp, "alice@example.com")

        created = await client.post(
            "/api/projects",
            json={
                "org_id": org_id,
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

        listing = await client.get("/api/projects")
        assert listing.status_code == 200
        assert any(p["id"] == project_id for p in listing.json())

        single = await client.get(f"/api/projects/{project_id}")
        assert single.status_code == 200
        assert single.json()["display_name"] == "Demo Project"

        patched = await client.patch(
            f"/api/projects/{project_id}",
            json={"display_name": "Demo (renamed)"},
        )
        assert patched.status_code == 200
        assert patched.json()["display_name"] == "Demo (renamed)"

        archived = await client.delete(f"/api/projects/{project_id}")
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

        # Default list filters out archived rows.
        post_archive = await client.get("/api/projects")
        assert all(p["id"] != project_id for p in post_archive.json())
        with_archived = await client.get("/api/projects?include_archived=true")
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
        _user_id, org_id = await _login_and_get_org(client, proj_fx.idp, "alice@example.com")
        body = {
            "org_id": org_id,
            "slug": "twin",
            "display_name": "Twin",
            "git_remote_url": "https://example.com/twin.git",
        }
        first = await client.post("/api/projects", json=body)
        assert first.status_code == 201
        second = await client.post("/api/projects", json=body)
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "project.slug_taken"


@pytest.mark.asyncio
async def test_non_member_is_forbidden(proj_fx: _ProjFixture) -> None:
    # User A creates a project; user B logs in to a *different* org
    # (because second user gets a new default org seeded — actually
    # `_ensure_user` seeds default org once, so B joins the same org
    # automatically only as a non-member by virtue of having no
    # membership row). User B should not see / GET the project.
    async with AsyncClient(
        transport=ASGITransport(app=proj_fx.app), base_url="http://test"
    ) as client:
        _, org_id = await _login_and_get_org(client, proj_fx.idp, "alice@example.com")
        created = await client.post(
            "/api/projects",
            json={
                "org_id": org_id,
                "slug": "private",
                "display_name": "Private",
                "git_remote_url": "https://example.com/private.git",
            },
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        # Switch user.
        await client.post("/api/auth/logout")
        client.cookies.clear()
        await _login_and_get_org(client, proj_fx.idp, "bob@example.com")

        # Bob's list does not include alice's project; direct GET is 403.
        bob_list = await client.get("/api/projects")
        assert bob_list.status_code == 200
        assert all(p["id"] != project_id for p in bob_list.json())
        forbidden = await client.get(f"/api/projects/{project_id}")
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["code"] == "project.forbidden"


@pytest.mark.asyncio
async def test_environment_crud(proj_fx: _ProjFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=proj_fx.app), base_url="http://test"
    ) as client:
        _, org_id = await _login_and_get_org(client, proj_fx.idp, "alice@example.com")
        created = await client.post(
            "/api/projects",
            json={
                "org_id": org_id,
                "slug": "envtest",
                "display_name": "Env Test",
                "git_remote_url": "https://example.com/envtest.git",
            },
        )
        project_id = created.json()["id"]

        env = await client.post(
            f"/api/projects/{project_id}/environments",
            json={
                "name": "dev",
                "deploy_target_kind": "local",
                "deploy_target_config": {"port": 3000},
            },
        )
        assert env.status_code == 201, env.text
        assert env.json()["name"] == "dev"

        listing = await client.get(f"/api/projects/{project_id}/environments")
        assert listing.status_code == 200
        assert [e["name"] for e in listing.json()] == ["dev"]

        # Duplicate name = 409.
        dup = await client.post(
            f"/api/projects/{project_id}/environments",
            json={"name": "dev", "deploy_target_kind": "local"},
        )
        assert dup.status_code == 409
        assert dup.json()["detail"]["code"] == "environment.name_taken"
