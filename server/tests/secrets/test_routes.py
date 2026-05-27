"""HTTP-level tests for /_gapt/api/secrets — auth + plaintext never leaks."""

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
from gapt_server.domains.secrets.backend import EncryptedSqliteBackend
from gapt_server.domains.secrets.vault import SecretVault
from gapt_server.routers.secrets import set_vault
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
class _RoutesFixture:
    app: FastAPI
    vault: SecretVault
    sqlite_path: Path
    admin_id: str


@pytest_asyncio.fixture
async def routes_fx(tmp_path: Path) -> AsyncIterator[_RoutesFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn, auth_enabled=False)  # type: ignore[arg-type]
    container = build_container(settings)
    app = create_app(settings=settings, container=container)

    sqlite_path = tmp_path / "vault.sqlite3"
    backend = EncryptedSqliteBackend(db_path=sqlite_path, master_key="test")
    vault = SecretVault(backend)
    set_vault(vault)

    try:
        yield _RoutesFixture(
            app=app, vault=vault, sqlite_path=sqlite_path, admin_id=settings.admin_id
        )
    finally:
        set_vault(None)  # type: ignore[arg-type]
        await container.aclose()


@pytest.mark.asyncio
async def test_secrets_require_auth() -> None:
    """A separate auth_enabled=True app makes sure /_gapt/api/secrets is gated."""
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    settings = Settings(postgres_dsn=sync_dsn)
    container = build_container(settings)
    app = create_app(settings=settings, container=container)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            unauth = await client.get("/_gapt/api/secrets")
            assert unauth.status_code == 401
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_full_secret_lifecycle_via_http(routes_fx: _RoutesFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=routes_fx.app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/_gapt/api/secrets",
            json={
                "scope": "system",
                "owner_id": routes_fx.admin_id,
                "key_name": "anthropic",
                "value": "sk-LIVE-DO-NOT-LEAK",
            },
        )
        assert created.status_code == 201, created.text
        view = created.json()
        secret_id = view["id"]
        # The response carries metadata only — no value field.
        assert "value" not in view
        assert view["key_name"] == "anthropic"

        listing = await client.get("/_gapt/api/secrets")
        assert listing.status_code == 200
        items = listing.json()
        assert len(items) == 1
        # Plaintext does not appear anywhere in the listing payload.
        assert "sk-LIVE-DO-NOT-LEAK" not in listing.text

        single = await client.get(f"/_gapt/api/secrets/{secret_id}")
        assert single.status_code == 200
        assert "value" not in single.json()
        assert "sk-LIVE-DO-NOT-LEAK" not in single.text

        rotated = await client.post(
            f"/_gapt/api/secrets/{secret_id}/rotate", json={"value": "sk-LIVE-v2"}
        )
        assert rotated.status_code == 200
        assert rotated.json()["rotated_at"] is not None

        deleted = await client.delete(f"/_gapt/api/secrets/{secret_id}")
        assert deleted.status_code == 204

        missing = await client.get(f"/_gapt/api/secrets/{secret_id}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_secret_returns_409(routes_fx: _RoutesFixture) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=routes_fx.app), base_url="http://test"
    ) as client:
        payload = {
            "scope": "system",
            "owner_id": routes_fx.admin_id,
            "key_name": "dup",
            "value": "v1",
        }
        first = await client.post("/_gapt/api/secrets", json=payload)
        assert first.status_code == 201

        payload["value"] = "v2"
        second = await client.post("/_gapt/api/secrets", json=payload)
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "secret.duplicate"
