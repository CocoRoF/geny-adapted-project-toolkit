"""HTTP-level tests for /api/notifications."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
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
from gapt_server.domains.auth.session import InMemorySessionStore
from gapt_server.domains.sandbox import MockSandboxBackend
from gapt_server.routers.auth import set_session_store
from gapt_server.settings import Settings

if TYPE_CHECKING:
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
class _Fx:
    app: FastAPI


@pytest_asyncio.fixture
async def fx() -> AsyncIterator[_Fx]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    # auth_enabled=True so we can exercise the 401-without-cookie path.
    settings = Settings(postgres_dsn=sync_dsn)
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    # Pin a fresh session store so sibling tests don't carry cookies in.
    set_session_store(InMemorySessionStore())
    app = create_app(settings=settings, container=container)
    try:
        yield _Fx(app=app)
    finally:
        await container.aclose()


async def _login_as_admin(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/login", json={"id": "admin", "password": "admin"}
    )
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_test_endpoint_appends_to_feed(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _login_as_admin(client)

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
async def test_unauthenticated_rejected(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        resp = await client.get("/api/notifications")
        assert resp.status_code == 401
