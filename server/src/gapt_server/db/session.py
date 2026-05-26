"""Async engine + session factory helpers.

Centralised here so both FastAPI dependency injection and Alembic env
construct engines the same way.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Build the app's async engine.

    Pool tuning rationale (2026-05): SQLAlchemy's default 5+10 was
    too tight for a control plane with multiple polling UI surfaces
    (Performance dashboard 3s, Cost summary, notifications bell,
    Logs modal 2.5s …) running across one or more open browser tabs.
    With slow endpoints (Performance does ~1 s of docker-stats
    sampling while holding the session) the queue saturated within
    seconds and unrelated endpoints started 500'ing with QueuePool
    TimeoutError.

    `pool_recycle=300` flushes idle connections every 5 min so
    they don't go stale behind the Postgres `idle_in_transaction`
    timeout; `pool_pre_ping` already covers per-checkout liveness.
    """
    return create_async_engine(
        dsn,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
        pool_recycle=300,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
