"""Single-admin login flow tests.

Covers the MinIO/Jenkins-style POST /_gapt/api/auth/login + cookie-issued
session model that replaced the old magic-link IDP. Hermetic — no
Postgres needed, the session store is an in-memory singleton.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.domains.auth.session import InMemorySessionStore
from gapt_server.routers.auth import set_session_store
from gapt_server.settings import Settings


def _make_settings(*, auth_enabled: bool = True) -> Settings:
    return Settings(
        env="dev",
        log_level="WARNING",
        log_format="console",
        session_secret="test-secret",
        daemon_jwt_secret="test-daemon",
        auth_enabled=auth_enabled,
    )


async def _client(settings: Settings) -> AsyncIterator[AsyncClient]:
    # Pin a fresh session store per test so cookies from one case
    # don't survive into the next.
    set_session_store(InMemorySessionStore())
    app = create_app(settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def auth_client() -> AsyncIterator[AsyncClient]:
    async for ac in _client(_make_settings()):
        yield ac


@pytest.fixture
async def open_client() -> AsyncIterator[AsyncClient]:
    """Client built with auth_enabled=False — every request auto-passes."""
    async for ac in _client(_make_settings(auth_enabled=False)):
        yield ac


@pytest.mark.asyncio
async def test_login_with_correct_credentials_sets_cookie(
    auth_client: AsyncClient,
) -> None:
    resp = await auth_client.post(
        "/_gapt/api/auth/login", json={"id": "admin", "password": "admin"}
    )
    assert resp.status_code == 204, resp.text
    assert auth_client.cookies.get("gapt_session"), "session cookie should be set"


@pytest.mark.asyncio
async def test_login_with_wrong_credentials_returns_401(
    auth_client: AsyncClient,
) -> None:
    resp = await auth_client.post(
        "/_gapt/api/auth/login", json={"id": "admin", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "auth.invalid_credentials"


@pytest.mark.asyncio
async def test_me_without_cookie_returns_401(auth_client: AsyncClient) -> None:
    resp = await auth_client.get("/_gapt/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "auth.session.missing"


@pytest.mark.asyncio
async def test_me_with_cookie_returns_admin(auth_client: AsyncClient) -> None:
    login = await auth_client.post(
        "/_gapt/api/auth/login", json={"id": "admin", "password": "admin"}
    )
    assert login.status_code == 204

    resp = await auth_client.get("/_gapt/api/auth/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == "admin"
    assert body["auth_enabled"] is True


@pytest.mark.asyncio
async def test_me_when_auth_disabled_passes_without_cookie(
    open_client: AsyncClient,
) -> None:
    resp = await open_client.get("/_gapt/api/auth/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == "admin"
    assert body["auth_enabled"] is False


@pytest.mark.asyncio
async def test_logout_clears_cookie_and_subsequent_me_is_401(
    auth_client: AsyncClient,
) -> None:
    login = await auth_client.post(
        "/_gapt/api/auth/login", json={"id": "admin", "password": "admin"}
    )
    assert login.status_code == 204
    # Sanity: while the cookie is valid the /me path works.
    me_ok = await auth_client.get("/_gapt/api/auth/me")
    assert me_ok.status_code == 200

    out = await auth_client.post("/_gapt/api/auth/logout")
    assert out.status_code == 204

    me = await auth_client.get("/_gapt/api/auth/me")
    assert me.status_code == 401
    # The cookie itself was cleared — /me sees no session at all.
    assert me.json()["detail"]["code"] == "auth.session.missing"
