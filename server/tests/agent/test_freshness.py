"""FreshnessPolicy + FreshnessRunner — pure unit + Postgres-backed sweep."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import select

from gapt_server.agent import (
    FreshnessAction,
    FreshnessPolicy,
    FreshnessRunner,
    FreshnessThresholds,
)
from gapt_server.db import create_engine, create_session_factory, enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import InMemoryAuditSink
from tests._helpers.db_guard import assert_safe_to_reset

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

SERVER_ROOT = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────── pure policy ──


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (timedelta(minutes=1), FreshnessAction.KEEP_ACTIVE),
        (timedelta(minutes=10), FreshnessAction.KEEP_ACTIVE),
        (timedelta(minutes=29, seconds=50), FreshnessAction.KEEP_ACTIVE),
        (timedelta(minutes=30), FreshnessAction.NOTIFY_IDLE),
        (timedelta(hours=1), FreshnessAction.NOTIFY_IDLE),
        (timedelta(hours=6), FreshnessAction.PAUSE_SANDBOX),
        (timedelta(hours=12), FreshnessAction.PAUSE_SANDBOX),
        (timedelta(hours=24), FreshnessAction.ARCHIVE),
        (timedelta(days=7), FreshnessAction.ARCHIVE),
    ],
)
def test_policy_band_boundaries(elapsed: timedelta, expected: FreshnessAction) -> None:
    policy = FreshnessPolicy()
    now = datetime.now(tz=UTC)
    last = now - elapsed
    action = policy.evaluate(
        last_active_at=last,
        current_status=enums.AgentSessionStatus.ACTIVE,
        now=now,
    )
    assert action is expected


def test_policy_idempotent_for_already_idle() -> None:
    """A session already in STALE_IDLE doesn't re-emit NOTIFY_IDLE."""
    policy = FreshnessPolicy()
    now = datetime.now(tz=UTC)
    last = now - timedelta(hours=1)
    action = policy.evaluate(
        last_active_at=last,
        current_status=enums.AgentSessionStatus.STALE_IDLE,
        now=now,
    )
    assert action is FreshnessAction.KEEP_ACTIVE


def test_policy_idempotent_for_already_compact() -> None:
    policy = FreshnessPolicy()
    now = datetime.now(tz=UTC)
    last = now - timedelta(hours=8)
    action = policy.evaluate(
        last_active_at=last,
        current_status=enums.AgentSessionStatus.STALE_COMPACT,
        now=now,
    )
    assert action is FreshnessAction.KEEP_ACTIVE


def test_policy_skips_archived() -> None:
    policy = FreshnessPolicy()
    now = datetime.now(tz=UTC)
    action = policy.evaluate(
        last_active_at=now - timedelta(days=30),
        current_status=enums.AgentSessionStatus.ARCHIVED,
        now=now,
    )
    assert action is FreshnessAction.KEEP_ACTIVE


def test_custom_thresholds() -> None:
    policy = FreshnessPolicy(
        thresholds=FreshnessThresholds(idle_notice_s=10.0, pause_s=60.0, archive_s=300.0)
    )
    now = datetime.now(tz=UTC)
    assert (
        policy.evaluate(
            last_active_at=now - timedelta(seconds=15),
            current_status=enums.AgentSessionStatus.ACTIVE,
            now=now,
        )
        is FreshnessAction.NOTIFY_IDLE
    )
    assert (
        policy.evaluate(
            last_active_at=now - timedelta(seconds=120),
            current_status=enums.AgentSessionStatus.ACTIVE,
            now=now,
        )
        is FreshnessAction.PAUSE_SANDBOX
    )


# ───────────────────────────────────────── runner (Postgres) ──


def _require_dsn() -> str:
    dsn = os.environ.get("GAPT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("GAPT_TEST_POSTGRES_DSN unset")
    return dsn


def _reset_and_upgrade(sync_dsn: str) -> None:
    assert_safe_to_reset(sync_dsn)
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
class _FrFixture:
    engine: AsyncEngine
    factory: async_sessionmaker
    audit: InMemoryAuditSink
    project: models.Project
    workspace: models.Workspace


async def _seed(factory) -> tuple[models.Project, models.Workspace]:  # type: ignore[no-untyped-def]
    async with factory() as db:
        project = models.Project(
            slug="demo",
            display_name="demo",
            git_remote_url="https://example.com/demo.git",
            git_provider=enums.GitProvider.GITHUB,
        )
        db.add(project)
        await db.flush()
        ws = models.Workspace(
            id=new_ulid(),
            project_id=project.id,
            name="main",
            worktree_path="/workspace",
            status=enums.WorkspaceStatus.RUNNING,
        )
        db.add(ws)
        await db.commit()
        return project, ws


@pytest_asyncio.fixture
async def fr_fx() -> AsyncIterator[_FrFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(async_dsn)
    factory = create_session_factory(engine)
    project, ws = await _seed(factory)
    try:
        yield _FrFixture(
            engine=engine,
            factory=factory,
            audit=InMemoryAuditSink(),
            project=project,
            workspace=ws,
        )
    finally:
        await engine.dispose()


async def _add_session(
    factory,  # type: ignore[no-untyped-def]
    *,
    project_id: str,
    workspace_id: str,
    last_active_at: datetime,
    status: enums.AgentSessionStatus = enums.AgentSessionStatus.ACTIVE,
) -> str:
    async with factory() as db:
        session = models.AgentSession(
            id=new_ulid(),
            project_id=project_id,
            workspace_id=workspace_id,
            env_manifest_id="gapt_default",
            status=status,
            last_active_at=last_active_at,
        )
        db.add(session)
        await db.commit()
        return session.id


@pytest.mark.asyncio
async def test_run_once_classifies_each_session(fr_fx: _FrFixture) -> None:
    now = datetime.now(tz=UTC)
    # 1 active, 1 idle, 1 pause, 1 archive
    await _add_session(
        fr_fx.factory,
        project_id=fr_fx.project.id,
        workspace_id=fr_fx.workspace.id,
        last_active_at=now - timedelta(minutes=1),
    )
    await _add_session(
        fr_fx.factory,
        project_id=fr_fx.project.id,
        workspace_id=fr_fx.workspace.id,
        last_active_at=now - timedelta(hours=1),
    )
    await _add_session(
        fr_fx.factory,
        project_id=fr_fx.project.id,
        workspace_id=fr_fx.workspace.id,
        last_active_at=now - timedelta(hours=7),
    )
    await _add_session(
        fr_fx.factory,
        project_id=fr_fx.project.id,
        workspace_id=fr_fx.workspace.id,
        last_active_at=now - timedelta(hours=25),
    )

    runner = FreshnessRunner(audit_sink=fr_fx.audit)
    async with fr_fx.factory() as db:
        counts = await runner.run_once(db, now=now)
        await db.commit()

    assert counts[FreshnessAction.KEEP_ACTIVE] == 1
    assert counts[FreshnessAction.NOTIFY_IDLE] == 1
    assert counts[FreshnessAction.PAUSE_SANDBOX] == 1
    assert counts[FreshnessAction.ARCHIVE] == 1

    # DB statuses updated correctly.
    async with fr_fx.factory() as db:
        rows = (
            (
                await db.execute(
                    select(models.AgentSession).order_by(models.AgentSession.last_active_at)
                )
            )
            .scalars()
            .all()
        )
    statuses = sorted(r.status.value for r in rows)
    assert statuses == ["active", "archived", "stale_compact", "stale_idle"]

    # Audit emitted for transitions only (3 events: idle_notice, pause, archive).
    actions = sorted(e.action for e in fr_fx.audit.events)
    assert actions == ["session.archive", "session.idle_notice", "session.pause"]


@pytest.mark.asyncio
async def test_runner_on_pause_callback_fires(fr_fx: _FrFixture) -> None:
    now = datetime.now(tz=UTC)
    session_id = await _add_session(
        fr_fx.factory,
        project_id=fr_fx.project.id,
        workspace_id=fr_fx.workspace.id,
        last_active_at=now - timedelta(hours=7),
    )

    paused: list[str] = []

    async def on_pause(sid: str) -> None:
        paused.append(sid)

    runner = FreshnessRunner(audit_sink=fr_fx.audit, on_pause=on_pause)
    async with fr_fx.factory() as db:
        await runner.run_once(db, now=now)
        await db.commit()
    assert paused == [session_id]


@pytest.mark.asyncio
async def test_runner_on_archive_callback_fires(fr_fx: _FrFixture) -> None:
    now = datetime.now(tz=UTC)
    session_id = await _add_session(
        fr_fx.factory,
        project_id=fr_fx.project.id,
        workspace_id=fr_fx.workspace.id,
        last_active_at=now - timedelta(hours=25),
    )

    archived: list[str] = []

    async def on_archive(sid: str) -> None:
        archived.append(sid)

    runner = FreshnessRunner(audit_sink=fr_fx.audit, on_archive=on_archive)
    async with fr_fx.factory() as db:
        await runner.run_once(db, now=now)
        await db.commit()
    assert archived == [session_id]


@pytest.mark.asyncio
async def test_runner_skips_already_archived(fr_fx: _FrFixture) -> None:
    now = datetime.now(tz=UTC)
    await _add_session(
        fr_fx.factory,
        project_id=fr_fx.project.id,
        workspace_id=fr_fx.workspace.id,
        last_active_at=now - timedelta(days=30),
        status=enums.AgentSessionStatus.ARCHIVED,
    )
    runner = FreshnessRunner(audit_sink=fr_fx.audit)
    async with fr_fx.factory() as db:
        counts = await runner.run_once(db, now=now)
    # Query filters out ARCHIVED rows, so no counts ticked.
    assert sum(counts.values()) == 0
    assert fr_fx.audit.events == []
