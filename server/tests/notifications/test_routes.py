"""HTTP-level tests for /api/notifications."""

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


async def _login(client: AsyncClient, fx: _Fx, email: str) -> str:
    await client.post("/api/auth/magic-link", json={"email": email})
    token = next(iter(fx.idp._tokens._items))  # type: ignore[attr-defined]
    cb = await client.get(f"/api/auth/magic-link/callback?token={token}")
    return cb.json()["user_id"]


@pytest.mark.asyncio
async def test_test_endpoint_appends_to_feed(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _login(client, fx, "alice@example.com")

        resp = await client.post(
            "/api/notifications/test",
            json={"title": "hello", "body": "world", "severity": "warn"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "hello"

        feed = await client.get("/api/notifications")
        assert feed.status_code == 200
        body = feed.json()
        assert len(body) == 1
        assert body[0]["severity"] == "warn"
        assert body[0]["title"] == "hello"


@pytest.mark.asyncio
async def test_feed_is_per_user(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _login(client, fx, "alice@example.com")
        await client.post("/api/notifications/test", json={"title": "alice-only"})

        # Switch to a fresh user.
        await client.post("/api/auth/logout")
        client.cookies.clear()
        await _login(client, fx, "mallory@example.com")

        feed = await client.get("/api/notifications")
        assert feed.status_code == 200
        # Mallory's bell is empty — alice's note isn't broadcast.
        assert feed.json() == []


@pytest.mark.asyncio
async def test_unauthenticated_rejected(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        resp = await client.get("/api/notifications")
        assert resp.status_code == 401
