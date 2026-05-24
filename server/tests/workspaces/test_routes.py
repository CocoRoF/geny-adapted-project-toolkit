"""HTTP-level tests for the workspace lifecycle endpoints.

Uses MockSandboxBackend so no docker/sysbox is required.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.db import models
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.auth.idp import build_memory_idp
from gapt_server.domains.sandbox import MockSandboxBackend, SandboxBackendError
from gapt_server.routers.auth import set_auth_idp
from gapt_server.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from gapt_server.domains.auth.idp import MagicLinkIdp

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
    idp: MagicLinkIdp
    audit: InMemoryAuditSink
    sandbox: MockSandboxBackend


@pytest_asyncio.fixture
async def fx(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Fx]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn)  # type: ignore[arg-type]
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)

    # Stub the host-side git clone so tests don't hit the real network
    # (the workspace tests use `https://example.com/demo.git` which is
    # not a valid repo and would block on retries).
    async def _noop_clone(
        _git_remote_url: str, _branch: str, _dest_dir: str
    ) -> tuple[int, str, str]:
        return (0, "stub clone\n", "")

    from gapt_server.domains.workspaces import service as ws_service

    monkeypatch.setattr(ws_service, "_default_clone_runner", _noop_clone)

    app = create_app(settings=settings, container=container)
    idp = build_memory_idp()
    set_auth_idp(idp)
    try:
        yield _Fx(app=app, idp=idp, audit=audit, sandbox=sandbox)
    finally:
        await container.aclose()


async def _login_and_create_project(client: AsyncClient, fx: _Fx, email: str) -> tuple[str, str]:
    """Logs in `email`, returns (user_id, project_id)."""
    await client.post("/api/auth/magic-link", json={"email": email})
    token = next(iter(fx.idp._tokens._items))
    cb = await client.get(f"/api/auth/magic-link/callback?token={token}")
    user_id = cb.json()["user_id"]

    container = client._transport.app.state.container  # type: ignore[attr-defined]
    async with container.session_factory() as db:  # type: ignore[union-attr]
        row = (
            await db.execute(
                select(models.OrgMembership).where(models.OrgMembership.user_id == user_id)
            )
        ).scalar_one()
    org_id = row.org_id

    created = await client.post(
        "/api/projects",
        json={
            "org_id": org_id,
            "slug": "demo",
            "display_name": "Demo",
            "git_remote_url": "https://example.com/demo.git",
        },
    )
    assert created.status_code == 201, created.text
    return user_id, created.json()["id"]


@pytest.mark.asyncio
async def test_workspace_full_lifecycle(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, project_id = await _login_and_create_project(client, fx, "alice@example.com")

        created = await client.post(
            f"/api/projects/{project_id}/workspaces",
            json={"branch": "main"},
        )
        assert created.status_code == 201, created.text
        wks = created.json()
        assert wks["branch"] == "main"
        # Background clone leaves the row in `creating` until it
        # finishes (RUNNING) or errors out (FAILED). Poll briefly.
        assert wks["status"] in ("creating", "running")
        assert wks["sandbox_id"] is not None
        workspace_id = wks["id"]

        # Wait for the background clone to finish so subsequent
        # transitions (stop/start/delete) operate on a settled state.
        for _ in range(40):  # ~2s max
            single = await client.get(f"/api/workspaces/{workspace_id}")
            if single.json()["status"] == "running":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("workspace never flipped to running")

        listed = await client.get(f"/api/projects/{project_id}/workspaces")
        assert listed.status_code == 200
        assert [w["id"] for w in listed.json()] == [workspace_id]

        single = await client.get(f"/api/workspaces/{workspace_id}")
        assert single.status_code == 200

        stopped = await client.post(f"/api/workspaces/{workspace_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"

        restarted = await client.post(f"/api/workspaces/{workspace_id}/start")
        assert restarted.status_code == 200
        assert restarted.json()["status"] == "running"

        deleted = await client.delete(f"/api/workspaces/{workspace_id}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "archived"

    actions = [e.action for e in fx.audit.events]
    assert "workspace.create" in actions
    assert "workspace.stop" in actions
    assert "workspace.delete" in actions


@pytest.mark.asyncio
async def test_create_workspace_requires_project_member(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        _, project_id = await _login_and_create_project(client, fx, "alice@example.com")
        # Switch to a different user with no project membership.
        await client.post("/api/auth/logout")
        client.cookies.clear()

        await client.post("/api/auth/magic-link", json={"email": "bob@example.com"})
        token = next(iter(fx.idp._tokens._items))
        await client.get(f"/api/auth/magic-link/callback?token={token}")

        forbidden = await client.post(
            f"/api/projects/{project_id}/workspaces",
            json={"branch": "main"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["code"] == "project.forbidden"


@pytest.mark.asyncio
async def test_sandbox_boot_failure_marks_workspace_failed(fx: _Fx) -> None:
    # Sabotage the mock backend so create() raises.
    original_create = fx.sandbox.create

    async def explode(*_args, **_kwargs):
        raise SandboxBackendError("docker daemon unreachable")

    fx.sandbox.create = explode  # type: ignore[assignment]

    try:
        async with AsyncClient(
            transport=ASGITransport(app=fx.app), base_url="http://test"
        ) as client:
            _, project_id = await _login_and_create_project(client, fx, "alice@example.com")
            resp = await client.post(
                f"/api/projects/{project_id}/workspaces",
                json={"branch": "main"},
            )
            assert resp.status_code == 409
            assert resp.json()["detail"]["code"] == "workspace.sandbox_boot_failed"

            # The row was still committed in FAILED state so the user can
            # see what went wrong.
            container = client._transport.app.state.container  # type: ignore[attr-defined]
            async with container.session_factory() as db:  # type: ignore[union-attr]
                row = (
                    await db.execute(
                        select(models.Workspace).where(models.Workspace.project_id == project_id)
                    )
                ).scalar_one()
                assert row.status.value == "failed"

        # Audit recorded the failure.
        actions = [(e.action, e.outcome.value) for e in fx.audit.events]
        assert ("workspace.create", "error") in actions
    finally:
        fx.sandbox.create = original_create  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_workspace_not_found_404(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _login_and_create_project(client, fx, "alice@example.com")
        resp = await client.get("/api/workspaces/01KSXXXXXXXXXXXXXXXXXXXXXX")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "workspace.not_found"
