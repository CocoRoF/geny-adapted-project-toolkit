"""HTTP-level tests for `GET /api/projects/{pid}/audit`."""

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
from gapt_server.db import enums, models
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.settings import Settings
from tests._helpers.fake_sandbox import FakeSandboxBackend

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
    settings = Settings(postgres_dsn=sync_dsn, auth_enabled=False)
    audit = InMemoryAuditSink()
    sandbox = FakeSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    app = create_app(settings=settings, container=container)
    try:
        yield _Fx(app=app)
    finally:
        await container.aclose()


async def _create_project(client: AsyncClient) -> str:
    created = await client.post(
        "/api/projects",
        json={
            "slug": "demo",
            "display_name": "Demo",
            "git_remote_url": "https://example.com/demo.git",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _seed_audit(app: FastAPI, project_id: str, count: int = 3) -> None:
    """Seed `count` audit events scoped to the project so the query
    endpoint has something to return."""
    container = app.state.container
    async with container.session_factory() as db:
        for i in range(count):
            row = models.AuditEvent(
                actor_type=enums.AuditActorType.USER,
                action=f"test.event.{i}",
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": project_id},
                subject={"i": i},
            )
            db.add(row)
        await db.commit()


@pytest.mark.asyncio
async def test_audit_lists_project_events(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id = await _create_project(client)
        await _seed_audit(fx.app, project_id, count=3)

        resp = await client.get(f"/api/projects/{project_id}/audit")
        assert resp.status_code == 200
        body = resp.json()
        # InMemoryAuditSink (the fixture's choice) keeps the
        # project.create audit in process, not in the DB — the
        # endpoint only sees the rows we seeded directly.
        assert len(body) == 3
        # Most recent first.
        assert body[0]["ts"] >= body[-1]["ts"]
        assert all(row["scope"]["project_id"] == project_id for row in body)


@pytest.mark.asyncio
async def test_audit_filters_by_action_prefix(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id = await _create_project(client)
        await _seed_audit(fx.app, project_id, count=2)

        resp = await client.get(
            f"/api/projects/{project_id}/audit?action_prefix=test.event."
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(row["action"].startswith("test.event.") for row in body)
        assert len(body) == 2


@pytest.mark.asyncio
async def test_audit_export_csv_round_trip(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id = await _create_project(client)
        await _seed_audit(fx.app, project_id, count=3)

        resp = await client.get(f"/api/projects/{project_id}/audit/export?format=csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        text = resp.text
        assert "id,ts,actor_type" in text  # header row
        # Three seeded rows + header.
        assert len(text.strip().splitlines()) == 4


@pytest.mark.asyncio
async def test_audit_export_jsonl_round_trip(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id = await _create_project(client)
        await _seed_audit(fx.app, project_id, count=2)

        resp = await client.get(f"/api/projects/{project_id}/audit/export?format=jsonl")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        lines = resp.text.strip().splitlines()
        assert len(lines) == 2
        import json as _json

        for line in lines:
            row = _json.loads(line)
            assert row["scope"]["project_id"] == project_id


@pytest.mark.asyncio
async def test_audit_export_respects_action_prefix(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        project_id = await _create_project(client)
        await _seed_audit(fx.app, project_id, count=4)

        resp = await client.get(
            f"/api/projects/{project_id}/audit/export"
            "?format=jsonl&action_prefix=test.event.1"
        )
        assert resp.status_code == 200
        lines = resp.text.strip().splitlines()
        # Only test.event.1 from {0,1,2,3} matches the prefix.
        assert len(lines) == 1
