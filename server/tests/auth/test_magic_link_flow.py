"""End-to-end magic-link flow tests.

Requires Postgres (via `GAPT_TEST_POSTGRES_DSN`) because the callback
inserts into `users` / `orgs` / `org_memberships`. We reset the schema
between tests for isolation.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.domains.auth.idp import MagicLinkIdp, build_memory_idp
from gapt_server.routers.auth import set_auth_idp
from gapt_server.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

SERVER_ROOT = Path(__file__).resolve().parents[2]


# ──────────────────────────────────────────────────────────── fixtures ──


@dataclass
class _CapturingDelivery:
    """Records the most recent callback URL so the test can replay it."""

    last: tuple[str, str] | None = field(default=None)

    async def deliver(self, *, email: str, callback_url: str) -> None:
        self.last = (email, callback_url)


@dataclass
class _AppFixture:
    app: FastAPI
    idp: MagicLinkIdp
    delivery: _CapturingDelivery


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


@pytest_asyncio.fixture
async def app_fx() -> AsyncIterator[_AppFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn)  # type: ignore[arg-type]
    container = build_container(settings)
    app = create_app(settings=settings, container=container)

    delivery = _CapturingDelivery()
    seed = build_memory_idp()
    idp = MagicLinkIdp(
        token_store=seed._tokens,
        session_store=seed._sessions,
        delivery=delivery,
    )
    set_auth_idp(idp)

    try:
        yield _AppFixture(app=app, idp=idp, delivery=delivery)
    finally:
        await container.aclose()


# ────────────────────────────────────────────────────────────── tests ──


@pytest.mark.asyncio
async def test_full_magic_link_flow_promotes_first_user_to_owner(app_fx: _AppFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_fx.app), base_url="http://test"
    ) as client:
        accepted = await client.post("/api/auth/magic-link", json={"email": "alice@example.com"})
        assert accepted.status_code == 202

        assert app_fx.delivery.last is not None
        email, callback_url = app_fx.delivery.last
        assert email == "alice@example.com"
        token = callback_url.rsplit("token=", 1)[-1]

        cb = await client.get(f"/api/auth/magic-link/callback?token={token}")
        assert cb.status_code == 200, cb.text
        assert cb.json()["email"] == "alice@example.com"
        assert client.cookies.get("gapt_session"), "session cookie should be set"

        me = await client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "alice@example.com"

        # Token was consumed — replaying it fails.
        replay = await client.get(f"/api/auth/magic-link/callback?token={token}")
        assert replay.status_code == 401
        assert replay.json()["detail"]["code"] == "auth.token.invalid"


@pytest.mark.asyncio
async def test_invalid_token_returns_401(app_fx: _AppFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_fx.app), base_url="http://test"
    ) as client:
        cb = await client.get("/api/auth/magic-link/callback?token=does-not-exist")
        assert cb.status_code == 401
        assert cb.json()["detail"]["code"] == "auth.token.invalid"


@pytest.mark.asyncio
async def test_me_without_cookie_returns_401(app_fx: _AppFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_fx.app), base_url="http://test"
    ) as client:
        me = await client.get("/api/auth/me")
        assert me.status_code == 401
        assert me.json()["detail"]["code"] == "auth.session.missing"


@pytest.mark.asyncio
async def test_logout_clears_session(app_fx: _AppFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_fx.app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/magic-link", json={"email": "bob@example.com"})
        token = next(iter(app_fx.idp._tokens._items))
        cb = await client.get(f"/api/auth/magic-link/callback?token={token}")
        assert cb.status_code == 200

        out = await client.post("/api/auth/logout")
        assert out.status_code == 204

        me = await client.get("/api/auth/me")
        assert me.status_code == 401
        # After logout, the cookie itself is cleared — /me sees no session
        # at all, which is `session.missing` rather than `session.expired`.
        assert me.json()["detail"]["code"] == "auth.session.missing"


@pytest.mark.asyncio
async def test_second_user_distinct_from_first(app_fx: _AppFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_fx.app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/magic-link", json={"email": "alice@example.com"})
        token1 = next(iter(app_fx.idp._tokens._items))
        cb1 = await client.get(f"/api/auth/magic-link/callback?token={token1}")
        assert cb1.status_code == 200
        first_user = cb1.json()["user_id"]

        await client.post("/api/auth/logout")
        await client.post("/api/auth/magic-link", json={"email": "carol@example.com"})
        token2 = next(iter(app_fx.idp._tokens._items))
        cb2 = await client.get(f"/api/auth/magic-link/callback?token={token2}")
        assert cb2.status_code == 200
        second_user = cb2.json()["user_id"]
        assert second_user != first_user
