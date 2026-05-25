"""ProjectAwareSessionManager — end-to-end on real Postgres."""

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
from sqlalchemy import select

from gapt_server.agent import (
    GaptEnvironmentService,
    ProjectAwareSessionManager,
    SessionManagerError,
)
from gapt_server.db import create_engine, create_session_factory, enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.auth import AdminPrincipal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

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
class _SmFixture:
    engine: AsyncEngine
    factory: async_sessionmaker
    audit: InMemoryAuditSink
    manager: ProjectAwareSessionManager
    admin: AdminPrincipal
    project: models.Project
    workspace: models.Workspace


async def _seed(factory):  # type: ignore[no-untyped-def]
    """Insert one project / workspace and return them."""
    async with factory() as db:
        project = models.Project(
            slug="demo",
            display_name="demo",
            git_remote_url="https://example.com/demo.git",
            git_provider=enums.GitProvider.GITHUB,
        )
        db.add(project)
        await db.flush()

        workspace = models.Workspace(
            id=new_ulid(),
            project_id=project.id,
            branch="main",
            worktree_path="/workspace",
            status=enums.WorkspaceStatus.RUNNING,
        )
        db.add(workspace)
        await db.commit()
        return project, workspace


@pytest_asyncio.fixture
async def sm_fx(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_SmFixture]:
    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(async_dsn)
    factory = create_session_factory(engine)

    audit = InMemoryAuditSink()
    env_svc = GaptEnvironmentService()
    manager = ProjectAwareSessionManager(env_service=env_svc, audit_sink=audit)
    admin = AdminPrincipal(id="admin", display_name="admin")

    project, workspace = await _seed(factory)
    try:
        yield _SmFixture(
            engine=engine,
            factory=factory,
            audit=audit,
            manager=manager,
            admin=admin,
            project=project,
            workspace=workspace,
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_session_happy_path(sm_fx: _SmFixture) -> None:
    async with sm_fx.factory() as db:
        handle = await sm_fx.manager.create_session(
            db,
            user=sm_fx.admin,
            workspace_id=sm_fx.workspace.id,
        )
        await db.commit()

    # Pipeline booted with 21 stages (M0-P3 contract).
    assert len(handle.pipeline.describe()) == 21
    assert handle.status == enums.AgentSessionStatus.ACTIVE

    # DB row persisted.
    async with sm_fx.factory() as db:
        row = (
            await db.execute(
                select(models.AgentSession).where(models.AgentSession.id == handle.session_id)
            )
        ).scalar_one()
        assert row.env_manifest_id == "gapt_default"
        assert row.status is enums.AgentSessionStatus.ACTIVE

    # Audit event emitted.
    actions = [e.action for e in sm_fx.audit.events]
    assert "session.create" in actions


@pytest.mark.asyncio
async def test_create_session_with_env_id(sm_fx: _SmFixture) -> None:
    async with sm_fx.factory() as db:
        handle = await sm_fx.manager.create_session(
            db,
            user=sm_fx.admin,
            workspace_id=sm_fx.workspace.id,
            env_id="gapt_review",
        )
        await db.commit()
    assert handle.env_manifest_id == "gapt_review"


@pytest.mark.asyncio
async def test_create_session_unknown_workspace(sm_fx: _SmFixture) -> None:
    async with sm_fx.factory() as db:
        with pytest.raises(SessionManagerError) as exc:
            await sm_fx.manager.create_session(
                db,
                user=sm_fx.admin,
                workspace_id="01KS90000000000000000XXXXX",
            )
        assert exc.value.code == "workspace.not_found"


@pytest.mark.asyncio
async def test_create_session_unknown_manifest(sm_fx: _SmFixture) -> None:
    async with sm_fx.factory() as db:
        with pytest.raises(SessionManagerError) as exc:
            await sm_fx.manager.create_session(
                db,
                user=sm_fx.admin,
                workspace_id=sm_fx.workspace.id,
                env_id="does_not_exist",
            )
        assert exc.value.code == "session.pipeline_boot_failed"


@pytest.mark.asyncio
async def test_archive_session(sm_fx: _SmFixture) -> None:
    async with sm_fx.factory() as db:
        handle = await sm_fx.manager.create_session(
            db,
            user=sm_fx.admin,
            workspace_id=sm_fx.workspace.id,
        )
        await db.commit()

    async with sm_fx.factory() as db:
        await sm_fx.manager.archive(db, user=sm_fx.admin, session_id=handle.session_id)
        await db.commit()

        row = (
            await db.execute(
                select(models.AgentSession).where(models.AgentSession.id == handle.session_id)
            )
        ).scalar_one()
        assert row.status is enums.AgentSessionStatus.ARCHIVED

    actions = [e.action for e in sm_fx.audit.events]
    assert "session.archive" in actions


@pytest.mark.asyncio
async def test_archive_unknown_session(sm_fx: _SmFixture) -> None:
    async with sm_fx.factory() as db:
        with pytest.raises(SessionManagerError) as exc:
            await sm_fx.manager.archive(
                db, user=sm_fx.admin, session_id="01KS90000000000000000XXXXX"
            )
        assert exc.value.code == "session.not_found"
