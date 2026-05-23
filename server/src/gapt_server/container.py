"""Application-scope dependency container.

A single source for things that live as long as the FastAPI process:
the SQLAlchemy engine and its `async_sessionmaker`. Per-request
adapters (audit sink, secret vault, etc.) hang off this container in
later cycles.

Why a container instead of module-level globals: the engine needs
explicit `.dispose()` on shutdown, and tests want to swap in an
overridden DSN without monkey-patching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003  — Depends inspects at runtime
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request
from sqlalchemy.ext.asyncio import (  # noqa: TC002  — dataclass + Depends inspect at runtime
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from gapt_server.db import create_engine, create_session_factory
from gapt_server.settings import Settings, get_settings


@dataclass
class AppContainer:
    settings: Settings
    engine: AsyncEngine | None
    session_factory: async_sessionmaker[AsyncSession] | None

    async def aclose(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


def build_container(settings: Settings) -> AppContainer:
    """Construct the container without performing any I/O.

    If `settings.postgres_dsn` is unset, the container is still usable
    for unit tests that don't touch the DB (e.g. /health). DB-dependent
    code paths will raise a clear error when they ask for a session.
    """
    if settings.postgres_dsn is None:
        return AppContainer(settings=settings, engine=None, session_factory=None)

    engine = create_engine(_coerce_async_dsn(str(settings.postgres_dsn)))
    factory = create_session_factory(engine)
    return AppContainer(settings=settings, engine=engine, session_factory=factory)


def _coerce_async_dsn(dsn: str) -> str:
    """Map driverless / sync DSN tokens to `postgresql+psycopg` so the
    SQLAlchemy async engine actually drives via psycopg's async path."""
    if dsn.startswith("postgresql+psycopg://"):
        return dsn
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


# ─────────────────────────────────────────────────────────── FastAPI Depends ─


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, AppContainer):
        # Fallback for tests that bypass create_app; provide a fresh
        # container based on env settings.
        container = build_container(get_settings())
        request.app.state.container = container
    return container


def get_app_settings(container: AppContainer = Depends(get_container)) -> Settings:
    return container.settings


async def get_db_session(
    container: AppContainer = Depends(get_container),
) -> AsyncIterator[AsyncSession]:
    if container.session_factory is None:
        raise RuntimeError(
            "Database is not configured (GAPT_POSTGRES_DSN unset). "
            "Set the DSN before using DB-backed endpoints."
        )
    async with container.session_factory() as session:
        yield session


def attach_container(app: FastAPI, container: AppContainer) -> None:
    """Attach the container to app.state. The caller is responsible for
    calling `await container.aclose()` on shutdown (done in lifespan)."""
    app.state.container = container
