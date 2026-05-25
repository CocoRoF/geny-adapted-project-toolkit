"""HTTP-level tests for /api/workspaces/{wid}/preview + /share."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.db import enums
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.caddy.share import parse_share_link
from gapt_server.domains.sandbox import MockSandboxBackend
from gapt_server.routers import preview as preview_router
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
class _Fx:
    app: FastAPI
    caddy_calls: list[tuple[str, str, Any]]


@pytest_asyncio.fixture
async def fx() -> AsyncIterator[_Fx]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(
        postgres_dsn=sync_dsn,
        caddy_admin_url="http://caddy.test:2019",
        caddy_preview_domain="preview.gapt.example",
        share_link_secret="test-secret",
        auth_enabled=False,
    )
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)

    # Stub the Caddy HTTP transport so no real network fires.
    caddy_calls: list[tuple[str, str, Any]] = []

    original_factory = preview_router._build_manager

    def stub_factory(s):  # type: ignore[no-untyped-def]
        from gapt_server.domains.caddy import CaddyAdminClient, SubdomainManager

        async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
            caddy_calls.append((method, path, body))
            return (200, None)

        client = CaddyAdminClient(transport=transport)
        return SubdomainManager(client=client, preview_domain=s.caddy_preview_domain or "")

    preview_router._build_manager = stub_factory  # type: ignore[assignment]

    app = create_app(settings=settings, container=container)
    try:
        yield _Fx(app=app, caddy_calls=caddy_calls)
    finally:
        preview_router._build_manager = original_factory  # type: ignore[assignment]
        await container.aclose()


async def _create_workspace(client: AsyncClient) -> str:
    """Returns workspace_id."""
    created = await client.post(
        "/api/projects",
        json={
            "slug": "demo",
            "display_name": "Demo",
            "git_remote_url": "https://example.com/demo.git",
        },
    )
    project_id = created.json()["id"]

    wks = await client.post(
        f"/api/projects/{project_id}/workspaces",
        json={"branch": "main"},
    )
    return wks.json()["id"]


@pytest.mark.asyncio
async def test_register_preview_posts_caddy_route(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        workspace_id = await _create_workspace(client)
        resp = await client.post(
            f"/api/workspaces/{workspace_id}/preview",
            json={"upstream_host": "10.0.0.5", "upstream_port": 3000},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["host"].endswith(".preview.gapt.example")
        # The stub captured exactly one POST to the routes path.
        methods = [m for (m, _, _) in fx.caddy_calls]
        paths = [p for (_, p, _) in fx.caddy_calls]
        assert methods == ["POST"]
        assert paths[0].endswith("/routes/...")


@pytest.mark.asyncio
async def test_unregister_preview_deletes_by_id(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        workspace_id = await _create_workspace(client)
        resp = await client.delete(f"/api/workspaces/{workspace_id}/preview")
        assert resp.status_code == 204
        methods = [m for (m, _, _) in fx.caddy_calls]
        assert methods == ["DELETE"]


@pytest.mark.asyncio
async def test_preview_disabled_when_caddy_unset() -> None:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    # No caddy_admin_url / preview_domain on this Settings.
    settings = Settings(postgres_dsn=sync_dsn, auth_enabled=False)
    audit = InMemoryAuditSink()
    sandbox = MockSandboxBackend()
    container = build_container(settings, audit_sink=audit, sandbox_backend=sandbox)
    app = create_app(settings=settings, container=container)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            workspace_id = await _create_workspace(client)
            resp = await client.post(
                f"/api/workspaces/{workspace_id}/preview",
                json={"upstream_host": "10.0.0.5", "upstream_port": 3000},
            )
            assert resp.status_code == 412
            assert resp.json()["detail"]["code"] == "preview.disabled"
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_share_link_round_trip(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        workspace_id = await _create_workspace(client)
        resp = await client.post(f"/api/workspaces/{workspace_id}/share?ttl_s=600")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["expires_in_s"] == 600
        # The token verifies with the same secret the fixture uses.
        recovered = parse_share_link(body["token"], secret="test-secret")
        assert recovered == workspace_id
        assert body["url"].startswith(f"https://{workspace_id.lower()}.preview.gapt.example/?share=")


@pytest.mark.asyncio
async def test_share_link_ttl_cap(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        workspace_id = await _create_workspace(client)
        resp = await client.post(
            f"/api/workspaces/{workspace_id}/share?ttl_s=999999",
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "share.ttl_too_long"


_ = (new_ulid, enums)  # keep imports alive for future cycles


@pytest.mark.asyncio
async def test_ask_approves_known_workspace_subdomain(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        workspace_id = await _create_workspace(client)
        domain = f"{workspace_id.lower()}.preview.gapt.example"
        # The ask endpoint must NOT require auth — Caddy calls it
        # unauthenticated.
        resp = await client.get(f"/api/preview/ask?domain={domain}")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"domain": domain}


@pytest.mark.asyncio
async def test_ask_rejects_unknown_slug(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        # No workspace seeded — any slug should return 404 so Caddy
        # refuses to mint a certificate.
        resp = await client.get("/api/preview/ask?domain=01k0123456789.preview.gapt.example")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "preview.unknown"


@pytest.mark.asyncio
async def test_ask_rejects_wrong_parent_domain(fx: _Fx) -> None:
    async with AsyncClient(transport=ASGITransport(app=fx.app), base_url="http://test") as client:
        resp = await client.get("/api/preview/ask?domain=evil.elsewhere.com")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "preview.wrong_domain"
