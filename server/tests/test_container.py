"""AppContainer + DSN coercion + Depends wiring."""

from __future__ import annotations

import os

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.container import (
    _coerce_async_dsn,
    build_container,
    get_app_settings,
    get_container,
    get_db_session,
)
from gapt_server.settings import Settings


def test_dsn_coercion_variants() -> None:
    assert _coerce_async_dsn("postgresql://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert (
        _coerce_async_dsn("postgresql+asyncpg://u:p@h:5432/db")
        == "postgresql+psycopg://u:p@h:5432/db"
    )
    # Already the right driver — pass through.
    assert (
        _coerce_async_dsn("postgresql+psycopg://u:p@h:5432/db")
        == "postgresql+psycopg://u:p@h:5432/db"
    )
    # Non-postgres scheme is not our concern.
    assert _coerce_async_dsn("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"


def test_container_without_postgres_is_usable() -> None:
    settings = Settings(postgres_dsn=None)
    container = build_container(settings)
    assert container.engine is None
    assert container.session_factory is None


@pytest.mark.asyncio
async def test_container_with_postgres_builds_engine() -> None:
    dsn = os.environ.get("GAPT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("GAPT_TEST_POSTGRES_DSN unset")
    settings = Settings(postgres_dsn=dsn)  # type: ignore[arg-type]
    container = build_container(settings)
    try:
        assert container.engine is not None
        assert container.session_factory is not None
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_get_db_session_raises_without_dsn() -> None:
    """The Depends factory raises a clear RuntimeError when no DSN is
    configured, instead of silently yielding `None` and crashing
    downstream with a NoneType error."""
    settings = Settings(postgres_dsn=None)
    container = build_container(settings)
    agen = get_db_session(container=container)
    with pytest.raises(RuntimeError, match="Database is not configured"):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_get_app_settings_via_depends() -> None:
    settings = Settings(postgres_dsn=None)
    app = create_app(settings=settings)

    @app.get("/__settings")
    async def read_settings(
        s: Settings = Depends(get_app_settings),  # noqa: B008
    ) -> dict[str, str]:
        return {"env": s.env, "log_format": s.log_format}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/__settings")
    assert response.status_code == 200
    body = response.json()
    assert body["env"] == "dev"
    assert body["log_format"] == "json"


def test_get_container_unconfigured_falls_back() -> None:
    """If a test bypasses create_app and hits get_container with an app
    that never had attach_container called, the dep should still build
    a container from env settings."""

    class _StubApp:
        class state:
            pass

    class _StubReq:
        app = _StubApp()

    container = get_container(_StubReq())  # type: ignore[arg-type]
    assert container.settings.env == "dev"
