"""HTTP-level: `GET /api/policies` returns the merged table."""

from __future__ import annotations

import os
import subprocess
import textwrap
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
from gapt_server.domains.auth.idp import build_memory_idp
from gapt_server.domains.sandbox import MockSandboxBackend
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


@pytest_asyncio.fixture
async def fx(tmp_path: Path) -> AsyncIterator[_Fx]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)

    # Drop a YAML override file so we can verify the route reports it.
    config = tmp_path / "policies.yaml"
    config.write_text(
        textwrap.dedent(
            """
            actions:
              git.push.protected:
                decision: allow
                reason: local CI is the gate
            """
        ).strip()
    )

    settings = Settings(
        postgres_dsn=sync_dsn,
        policy_config_path=str(config),
    )
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    app = create_app(settings=settings, container=container)
    idp = build_memory_idp()
    set_auth_idp(idp)
    try:
        yield _Fx(app=app, idp=idp)
    finally:
        await container.aclose()


async def _login(client: AsyncClient, fx: _Fx, email: str) -> None:
    await client.post("/api/auth/magic-link", json={"email": email})
    token = next(iter(fx.idp._tokens._items))  # type: ignore[attr-defined]
    await client.get(f"/api/auth/magic-link/callback?token={token}")


@pytest.mark.asyncio
async def test_get_policies_returns_merged_table_with_source_layers(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        await _login(client, fx, "alice@example.com")
        resp = await client.get("/api/policies")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        rows = {r["action"]: r for r in body["rows"]}
        # Overridden action shows source=server.
        assert rows["git.push.protected"]["source"] == "server"
        assert rows["git.push.protected"]["decision"] == "allow"
        assert "local CI" in rows["git.push.protected"]["reason"]
        # Non-overridden action keeps source=builtin.
        assert rows["secret.create"]["source"] == "builtin"
        assert rows["secret.create"]["decision"] == "deny"
        # Invariant floors come back so the UI can paint them.
        assert "deploy.prod" in body["invariants"]


@pytest.mark.asyncio
async def test_get_policies_requires_auth(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        resp = await client.get("/api/policies")
        assert resp.status_code == 401
