"""ORM smoke — insert a small object graph and read it back.

Touches every primary model so the SQLAlchemy ↔ Postgres mapping stays
in lockstep with the migration. Like `test_migration` this test skips
when `GAPT_TEST_POSTGRES_DSN` is unset.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gapt_server.db import create_engine, create_session_factory, enums, models

SERVER_ROOT = Path(__file__).resolve().parents[2]


def _dsn_sync() -> str:
    dsn = os.environ.get("GAPT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("GAPT_TEST_POSTGRES_DSN unset")
    return dsn.replace("postgresql+asyncpg", "postgresql", 1).replace(
        "postgresql+psycopg", "postgresql", 1
    )


def _dsn_async(sync_dsn: str) -> str:
    """psycopg 3.x supports both sync and async; same install, different driver token."""
    return sync_dsn.replace("postgresql://", "postgresql+psycopg://", 1)


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


async def _exercise(async_dsn: str) -> None:
    engine = create_engine(async_dsn)
    factory = create_session_factory(engine)

    try:
        async with factory() as session:
            assert isinstance(session, AsyncSession)
            user = models.User(email="alice@example.com", display_name="Alice")
            org = models.Org(slug="default", name="Default Org", owner_id="placeholder")
            session.add(user)
            await session.flush()  # user.id populated
            org.owner_id = user.id
            session.add(org)
            await session.flush()

            project = models.Project(
                slug="demo",
                org_id=org.id,
                owner_id=user.id,
                display_name="Demo project",
                git_remote_url="https://github.com/CocoRoF/demo.git",
                git_provider=enums.GitProvider.GITHUB,
                default_compose_paths=["compose.dev.yml"],
            )
            session.add(project)
            await session.flush()

            audit = models.AuditEvent(
                actor_type=enums.AuditActorType.USER,
                actor_id=user.id,
                scope={"project_id": project.id},
                action="project.create",
                subject={"display_name": project.display_name},
                outcome=enums.AuditOutcome.OK,
                payload={"git_remote_url": project.git_remote_url},
            )
            session.add(audit)
            await session.commit()

        async with factory() as session:
            result = await session.execute(
                select(models.Project).where(models.Project.slug == "demo")
            )
            fetched = result.scalar_one()
            assert fetched.display_name == "Demo project"
            assert fetched.git_provider is enums.GitProvider.GITHUB
            assert fetched.default_compose_paths == ["compose.dev.yml"]
            assert fetched.archived_at is None

            audit_count = (
                (
                    await session.execute(
                        select(models.AuditEvent).where(
                            models.AuditEvent.action == "project.create"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(audit_count) == 1
            assert audit_count[0].outcome is enums.AuditOutcome.OK
    finally:
        await engine.dispose()


def test_orm_roundtrip() -> None:
    sync_dsn = _dsn_sync()
    async_dsn = _dsn_async(sync_dsn)
    _reset_and_upgrade(sync_dsn)
    asyncio.run(_exercise(async_dsn))
