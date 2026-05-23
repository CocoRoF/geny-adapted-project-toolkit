"""HTTP-level tests for the cost dashboard endpoints."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.db import enums, models
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.auth.idp import build_memory_idp
from gapt_server.domains.sandbox import MockSandboxBackend
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
class _Fx:
    app: FastAPI
    idp: MagicLinkIdp


@pytest_asyncio.fixture
async def fx() -> AsyncIterator[_Fx]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn)
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    app = create_app(settings=settings, container=container)
    idp = build_memory_idp()
    set_auth_idp(idp)
    try:
        yield _Fx(app=app, idp=idp)
    finally:
        await container.aclose()


async def _login_and_create_project(client: AsyncClient, fx: _Fx, email: str) -> tuple[str, str]:
    """Login + create one project. Returns (user_id, project_id)."""
    await client.post("/api/auth/magic-link", json={"email": email})
    token = next(iter(fx.idp._tokens._items))  # type: ignore[attr-defined]
    cb = await client.get(f"/api/auth/magic-link/callback?token={token}")
    user_id = cb.json()["user_id"]

    container = client._transport.app.state.container  # type: ignore[attr-defined]
    async with container.session_factory() as db:
        org_id = (
            await db.execute(
                select(models.OrgMembership.org_id).where(
                    models.OrgMembership.user_id == user_id
                )
            )
        ).scalar_one()

    created = await client.post(
        "/api/projects",
        json={
            "org_id": org_id,
            "slug": "demo",
            "display_name": "Demo",
            "git_remote_url": "https://example.com/demo.git",
        },
    )
    assert created.status_code == 201, created.text
    return user_id, created.json()["id"]


async def _seed_session(
    app: FastAPI,
    *,
    project_id: str,
    user_id: str,
    cost: float,
    in_tokens: int,
    out_tokens: int,
    when: datetime | None = None,
) -> None:
    container = app.state.container
    async with container.session_factory() as db:
        # The session needs a workspace — make a throwaway one.
        ws = models.Workspace(
            project_id=project_id,
            branch="main",
            worktree_path=f"/tmp/ws-{cost}",
            status=enums.WorkspaceStatus.RUNNING,
        )
        db.add(ws)
        await db.flush()

        row = models.AgentSession(
            project_id=project_id,
            workspace_id=ws.id,
            user_id=user_id,
            env_manifest_id="default",
            status=enums.AgentSessionStatus.ACTIVE,
            cost_usd=cost,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
        if when is not None:
            row.created_at = when
        db.add(row)
        await db.commit()


@pytest.mark.asyncio
async def test_summary_rolls_up_per_project(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        user_id, project_id = await _login_and_create_project(client, fx, "alice@example.com")
        await _seed_session(
            fx.app, project_id=project_id, user_id=user_id,
            cost=0.5, in_tokens=100, out_tokens=50,
        )
        await _seed_session(
            fx.app, project_id=project_id, user_id=user_id,
            cost=0.25, in_tokens=60, out_tokens=20,
        )

        resp = await client.get("/api/cost/summary")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_cost_usd"] == 0.75
        assert body["total_input_tokens"] == 160
        assert body["total_output_tokens"] == 70
        assert len(body["rows"]) == 1
        assert body["rows"][0]["project_id"] == project_id
        assert body["rows"][0]["session_count"] == 2


@pytest.mark.asyncio
async def test_summary_excludes_other_users_projects(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        # Alice's project + session.
        a_user, a_project = await _login_and_create_project(client, fx, "alice@example.com")
        await _seed_session(
            fx.app, project_id=a_project, user_id=a_user,
            cost=1.0, in_tokens=100, out_tokens=50,
        )

        # Mallory logs in fresh — should see no costs.
        await client.post("/api/auth/logout")
        client.cookies.clear()
        await client.post("/api/auth/magic-link", json={"email": "mallory@example.com"})
        token = next(iter(fx.idp._tokens._items))  # type: ignore[attr-defined]
        await client.get(f"/api/auth/magic-link/callback?token={token}")

        resp = await client.get("/api/cost/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows"] == []
        assert body["total_cost_usd"] == 0


@pytest.mark.asyncio
async def test_summary_respects_since_until_window(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        user_id, project_id = await _login_and_create_project(client, fx, "alice@example.com")
        now = datetime.now(UTC)
        old = now - timedelta(days=30)
        await _seed_session(
            fx.app, project_id=project_id, user_id=user_id,
            cost=1.5, in_tokens=100, out_tokens=10, when=old,
        )
        await _seed_session(
            fx.app, project_id=project_id, user_id=user_id,
            cost=0.5, in_tokens=20, out_tokens=5,
        )

        since = (now - timedelta(days=7)).isoformat().replace("+", "%2B")
        resp = await client.get(f"/api/cost/summary?since={since}")
        assert resp.status_code == 200
        body = resp.json()
        # Only the recent session inside the window counts.
        assert body["total_cost_usd"] == 0.5
        assert body["rows"][0]["session_count"] == 1


@pytest.mark.asyncio
async def test_daily_buckets_per_project(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        user_id, project_id = await _login_and_create_project(client, fx, "alice@example.com")
        d1 = datetime(2026, 5, 20, 10, tzinfo=UTC)
        d2 = datetime(2026, 5, 22, 10, tzinfo=UTC)
        await _seed_session(
            fx.app, project_id=project_id, user_id=user_id,
            cost=0.10, in_tokens=10, out_tokens=5, when=d1,
        )
        await _seed_session(
            fx.app, project_id=project_id, user_id=user_id,
            cost=0.30, in_tokens=30, out_tokens=15, when=d2,
        )
        await _seed_session(
            fx.app, project_id=project_id, user_id=user_id,
            cost=0.05, in_tokens=4, out_tokens=2, when=d2,
        )

        resp = await client.get(f"/api/projects/{project_id}/cost/daily")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["date"] == "2026-05-20"
        assert body[0]["cost_usd"] == 0.10
        assert body[1]["date"] == "2026-05-22"
        assert body[1]["cost_usd"] == 0.35  # 0.30 + 0.05
        assert body[1]["session_count"] == 2


@pytest.mark.asyncio
async def test_daily_403_for_non_member(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, project_id = await _login_and_create_project(client, fx, "alice@example.com")
        await client.post("/api/auth/logout")
        client.cookies.clear()
        await client.post("/api/auth/magic-link", json={"email": "mallory@example.com"})
        token = next(iter(fx.idp._tokens._items))  # type: ignore[attr-defined]
        await client.get(f"/api/auth/magic-link/callback?token={token}")

        resp = await client.get(f"/api/projects/{project_id}/cost/daily")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_metrics_endpoint_renders_text_format(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        user_id, project_id = await _login_and_create_project(client, fx, "alice@example.com")
        await _seed_session(
            fx.app, project_id=project_id, user_id=user_id,
            cost=0.1, in_tokens=10, out_tokens=5,
        )

        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        text = resp.text
        assert "# TYPE gapt_sessions_active gauge" in text
        # We seeded one active session.
        assert "gapt_sessions_active 1" in text
        assert "# TYPE gapt_agent_cost_usd_total counter" in text
