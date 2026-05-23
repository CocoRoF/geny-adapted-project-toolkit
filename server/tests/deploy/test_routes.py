"""HTTP-level tests for `POST /api/environments/{env_id}/deploy` and
`/rollback`. Uses an injectable webhook poster so no real HTTP fires."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.auth.idp import build_memory_idp
from gapt_server.domains.deploy import AcceptAnyCodeVerifier, AlwaysDenyVerifier, WebhookTarget
from gapt_server.domains.sandbox import MockSandboxBackend
from gapt_server.routers import deploy as deploy_router
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
    webhook_responses: list[tuple[int, dict[str, Any]]]


@pytest_asyncio.fixture
async def fx() -> AsyncIterator[_Fx]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn)
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)

    # Inject a webhook target whose poster is fully scripted.
    responses: list[tuple[int, dict[str, Any]]] = [(200, {"status": "success"})]

    async def poster(
        url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        return responses[-1]

    def build_target_local(kind):  # type: ignore[no-untyped-def]
        return WebhookTarget(poster=poster)

    # Monkey-patch the router's target factory so every kind maps
    # to the webhook target. Production hot-swap pattern.
    original_factory = deploy_router._build_target
    deploy_router._build_target = build_target_local  # type: ignore[assignment]

    app = create_app(settings=settings, container=container)
    idp = build_memory_idp()
    set_auth_idp(idp)
    try:
        yield _Fx(app=app, idp=idp, webhook_responses=responses)
    finally:
        deploy_router._build_target = original_factory  # type: ignore[assignment]
        await container.aclose()


async def _login_with_project_env(client: AsyncClient, fx: _Fx, email: str) -> tuple[str, str]:
    """Returns (project_id, environment_id)."""
    await client.post("/api/auth/magic-link", json={"email": email})
    token = next(iter(fx.idp._tokens._items))  # type: ignore[attr-defined]
    cb = await client.get(f"/api/auth/magic-link/callback?token={token}")
    user_id = cb.json()["user_id"]

    container = client._transport.app.state.container  # type: ignore[attr-defined]
    async with container.session_factory() as db:
        org_id = (
            await db.execute(
                select(models.OrgMembership.org_id).where(models.OrgMembership.user_id == user_id)
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
    project_id = created.json()["id"]

    # Insert an Environment row directly (no Environment-CRUD router
    # ships yet — Cycle 4.x or later).
    env_id = new_ulid()
    async with container.session_factory() as db:
        env = models.Environment(
            id=env_id,
            project_id=project_id,
            name="dev",
            deploy_target_kind=enums.DeployTargetKind.WEBHOOK,
            deploy_target_config={
                "compose_path": "compose/dev.yml",
                "webhook": {
                    "url": "https://example.test/deploy",
                    "secret": "shhhh",
                },
            },
            require_2fa=False,
            secret_refs=[],
        )
        db.add(env)
        await db.commit()

    return project_id, env_id


@pytest.mark.asyncio
async def test_deploy_happy_path_returns_success(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, env_id = await _login_with_project_env(client, fx, "alice@example.com")
        resp = await client.post(
            f"/api/environments/{env_id}/deploy",
            json={"version": "v1"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success"
        assert body["exec_code"] is None


@pytest.mark.asyncio
async def test_deploy_webhook_failure_returns_exec_code(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id, env_id = await _login_with_project_env(client, fx, "alice@example.com")
        fx.webhook_responses[:] = [(502, {"error": "upstream"})]
        resp = await client.post(
            f"/api/environments/{env_id}/deploy",
            json={"version": "v1"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert body["exec_code"] == "deploy.webhook.http_502"


@pytest.mark.asyncio
async def test_deploy_404_when_environment_missing(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _login_with_project_env(client, fx, "alice@example.com")
        resp = await client.post(
            "/api/environments/01KSXXXXXXXXXXXXXXXXXXXXXX/deploy",
            json={"version": "v"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "environment.not_found"


@pytest.mark.asyncio
async def test_deploy_403_for_non_member(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, env_id = await _login_with_project_env(client, fx, "alice@example.com")
        await client.post("/api/auth/logout")
        client.cookies.clear()
        await client.post("/api/auth/magic-link", json={"email": "mallory@example.com"})
        token = next(iter(fx.idp._tokens._items))  # type: ignore[attr-defined]
        await client.get(f"/api/auth/magic-link/callback?token={token}")

        resp = await client.post(
            f"/api/environments/{env_id}/deploy",
            json={"version": "v"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "project.forbidden"


@pytest.mark.asyncio
async def test_rollback_round_trip(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, env_id = await _login_with_project_env(client, fx, "alice@example.com")
        resp = await client.post(
            f"/api/environments/{env_id}/rollback",
            json={"run_id": "rid-1", "to_version": "v0"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "rolled_back"
        assert body["restored_version"] == "v0"
