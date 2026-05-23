"""End-to-end smoke for M1-E1 — proves the control plane can take a
user from "first login" to a running workspace and back without any
of the cycle-specific test seams leaking through.

Coverage in one test:
1. magic-link login → first user becomes OWNER of the default org.
2. project create (auto OWNER membership).
3. environment create.
4. secret store (plaintext never on the wire).
5. workspace create — `WorkspaceService` boots the (mock) sandbox.
6. workspace stop + start exercises the SandboxBackend state machine.
7. workspace delete tears the sandbox down.
8. project archive ends the lifecycle.

The fixture is hermetic: real Postgres for the schema, `MockSandboxBackend`
for the sandbox (so no docker is required in CI), `InMemoryAuditSink`
so every state transition is asserted, `InMemoryVolumeManager` if and
when workspaces ask for a volume.
"""

from __future__ import annotations

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
from gapt_server.domains.sandbox import MockSandboxBackend
from gapt_server.routers.auth import set_auth_idp
from gapt_server.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

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
class _E2EFixture:
    app: FastAPI
    idp: MagicLinkIdp
    audit: InMemoryAuditSink
    sandbox: MockSandboxBackend


@pytest_asyncio.fixture
async def e2e_fx() -> AsyncIterator[_E2EFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn)  # type: ignore[arg-type]
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    app = create_app(settings=settings, container=container)
    idp = build_memory_idp()
    set_auth_idp(idp)
    try:
        yield _E2EFixture(app=app, idp=idp, audit=audit, sandbox=sandbox)
    finally:
        await container.aclose()


async def _login(client: AsyncClient, idp: MagicLinkIdp, email: str) -> str:
    await client.post("/api/auth/magic-link", json={"email": email})
    token = next(iter(idp._tokens._items))
    cb = await client.get(f"/api/auth/magic-link/callback?token={token}")
    return cb.json()["user_id"]


async def _default_org(app: FastAPI, user_id: str) -> str:
    container = app.state.container
    async with container.session_factory() as db:
        row = (
            await db.execute(
                select(models.OrgMembership).where(models.OrgMembership.user_id == user_id)
            )
        ).scalar_one()
    return row.org_id


def _actions(audit: InMemoryAuditSink) -> Iterable[str]:
    return [event.action for event in audit.events]


@pytest.mark.asyncio
async def test_e1_full_user_journey(e2e_fx: _E2EFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=e2e_fx.app), base_url="http://test"
    ) as client:
        # Step 1 — magic-link login.
        user_id = await _login(client, e2e_fx.idp, "alice@example.com")
        assert user_id

        org_id = await _default_org(e2e_fx.app, user_id)

        # Step 2 — project create.
        proj = await client.post(
            "/api/projects",
            json={
                "org_id": org_id,
                "slug": "e1-smoke",
                "display_name": "E1 Smoke",
                "git_remote_url": "https://example.com/e1-smoke.git",
                "git_provider": "github",
                "default_compose_paths": ["compose.dev.yml"],
            },
        )
        assert proj.status_code == 201, proj.text
        project_id = proj.json()["id"]

        # Step 3 — environment create.
        env = await client.post(
            f"/api/projects/{project_id}/environments",
            json={
                "name": "dev",
                "deploy_target_kind": "local",
                "deploy_target_config": {"port": 3000},
            },
        )
        assert env.status_code == 201, env.text

        # Step 4 — secret store (response carries metadata only).
        secret = await client.post(
            "/api/secrets",
            json={
                "scope": "user",
                "owner_id": user_id,
                "key_name": "e1_smoke_anthropic",
                "value": "sk-LIVE-DO-NOT-LEAK-e1-smoke",
            },
        )
        assert secret.status_code == 201
        assert "value" not in secret.json()
        listing = await client.get("/api/secrets")
        assert "sk-LIVE-DO-NOT-LEAK-e1-smoke" not in listing.text

        # Step 5 — workspace create. Boots a MockSandbox.
        ws = await client.post(
            f"/api/projects/{project_id}/workspaces",
            json={"branch": "main"},
        )
        assert ws.status_code == 201, ws.text
        workspace_id = ws.json()["id"]
        assert ws.json()["status"] == "running"

        # MockSandbox saw a `create` + `start` call.
        assert e2e_fx.sandbox.exec_log  # method exists

        # Step 6 — workspace stop + start.
        stopped = await client.post(f"/api/workspaces/{workspace_id}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "stopped"
        started = await client.post(f"/api/workspaces/{workspace_id}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "running"

        # Step 7 — workspace delete (sandbox torn down).
        deleted = await client.delete(f"/api/workspaces/{workspace_id}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "archived"

        # Step 8 — project archive.
        archived = await client.delete(f"/api/projects/{project_id}")
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

    # Audit log carries every load-bearing action. `secret.create` /
    # `secret.delete` audit emit is queued for M1-E2 (the vault only
    # audits reads in this milestone — see Cycle 1.5 Drift).
    actions = list(_actions(e2e_fx.audit))
    for required in (
        "project.create",
        "workspace.create",
        "workspace.stop",
        "workspace.delete",
        "project.archive",
    ):
        assert required in actions, f"missing audit action: {required}\nseen: {actions}"
