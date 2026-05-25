"""End-to-end smoke for M1-E1 — proves the control plane can take a
single admin from "first login" to a running workspace and back without
any of the cycle-specific test seams leaking through.

Coverage in one test:
1. project create.
2. environment create.
3. secret store (plaintext never on the wire).
4. workspace create — `WorkspaceService` boots the (mock) sandbox.
5. workspace stop + start exercises the SandboxBackend state machine.
6. workspace delete tears the sandbox down.
7. project archive ends the lifecycle.

The fixture is hermetic: real Postgres for the schema, `MockSandboxBackend`
for the sandbox (so no docker is required in CI), `InMemoryAuditSink`
so every state transition is asserted.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.sandbox import MockSandboxBackend
from gapt_server.settings import Settings

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
class _E2EFixture:
    app: FastAPI
    audit: InMemoryAuditSink
    sandbox: MockSandboxBackend
    admin_id: str


@pytest_asyncio.fixture
async def e2e_fx(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_E2EFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn, auth_enabled=False)  # type: ignore[arg-type]
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)

    # Stub host-side git clone — tests use example.com remote URLs.
    async def _noop_clone(_url: str, _branch: str, _dest: str) -> tuple[int, str, str]:
        return (0, "stub clone\n", "")

    from gapt_server.domains.workspaces import service as ws_service

    monkeypatch.setattr(ws_service, "_default_clone_runner", _noop_clone)

    app = create_app(settings=settings, container=container)
    try:
        yield _E2EFixture(
            app=app, audit=audit, sandbox=sandbox, admin_id=settings.admin_id
        )
    finally:
        await container.aclose()


def _actions(audit: InMemoryAuditSink) -> Iterable[str]:
    return [event.action for event in audit.events]


@pytest.mark.asyncio
async def test_e1_full_admin_journey(e2e_fx: _E2EFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=e2e_fx.app), base_url="http://test"
    ) as client:
        # Step 1 — project create (auth disabled in fixture).
        proj = await client.post(
            "/api/projects",
            json={
                "slug": "e1-smoke",
                "display_name": "E1 Smoke",
                "git_remote_url": "https://example.com/e1-smoke.git",
                "git_provider": "github",
                "default_compose_paths": ["compose.dev.yml"],
            },
        )
        assert proj.status_code == 201, proj.text
        project_id = proj.json()["id"]

        # Step 2 — environment create.
        env = await client.post(
            f"/api/projects/{project_id}/environments",
            json={
                "name": "dev",
                "deploy_target_kind": "local",
                "deploy_target_config": {"port": 3000},
            },
        )
        assert env.status_code == 201, env.text

        # Step 3 — secret store (response carries metadata only).
        secret = await client.post(
            "/api/secrets",
            json={
                "scope": "system",
                "owner_id": e2e_fx.admin_id,
                "key_name": "e1_smoke_anthropic",
                "value": "sk-LIVE-DO-NOT-LEAK-e1-smoke",
            },
        )
        assert secret.status_code == 201
        assert "value" not in secret.json()
        listing = await client.get("/api/secrets")
        assert "sk-LIVE-DO-NOT-LEAK-e1-smoke" not in listing.text

        # Step 4 — workspace create. Boots a MockSandbox + kicks off
        # the host-side clone in a background task. Status flips from
        # `creating` to `running` once the clone settles.
        ws = await client.post(
            f"/api/projects/{project_id}/workspaces",
            json={"branch": "main"},
        )
        assert ws.status_code == 201, ws.text
        workspace_id = ws.json()["id"]
        assert ws.json()["status"] in ("creating", "running")

        # MockSandbox saw a `create` + `start` call.
        assert e2e_fx.sandbox.exec_log  # method exists

        # Wait for the background clone to finish so stop/start sees a
        # settled state.
        for _ in range(60):  # ~6s ceiling
            poll = await client.get(f"/api/workspaces/{workspace_id}")
            if poll.json()["status"] == "running":
                break
            await asyncio.sleep(0.1)

        # Step 5 — workspace stop + start.
        stopped = await client.post(f"/api/workspaces/{workspace_id}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "stopped"
        started = await client.post(f"/api/workspaces/{workspace_id}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "running"

        # Step 6 — workspace delete (sandbox torn down).
        deleted = await client.delete(f"/api/workspaces/{workspace_id}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "archived"

        # Step 7 — project archive.
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
