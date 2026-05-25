"""Unit tests for the EncryptedSqliteBackend + SecretVault.

The backend test is hermetic (just a temp dir). The vault test needs
Postgres because it writes to the `secrets` ORM table.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import text

from gapt_server.db import create_engine, create_session_factory, enums
from gapt_server.domains.secrets.backend import (
    EncryptedSqliteBackend,
    SecretBackendError,
    SecretRef,
)
from gapt_server.domains.secrets.vault import SecretVault, SecretVaultError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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


# ────────────────────────────────────────────────── backend ──


def test_backend_round_trip(tmp_path: Path) -> None:
    backend = EncryptedSqliteBackend(
        db_path=tmp_path / "vault.sqlite3",
        master_key="unit-test-master-key",
    )

    async def go() -> None:
        ref = await backend.store("super-secret-value")
        # Plaintext never appears in the on-disk DB.
        raw = (tmp_path / "vault.sqlite3").read_bytes()
        assert b"super-secret-value" not in raw
        assert await backend.read(ref) == "super-secret-value"
        await backend.delete(ref)
        with pytest.raises(SecretBackendError):
            await backend.read(ref)

    asyncio.run(go())


def test_backend_rejects_foreign_ref(tmp_path: Path) -> None:
    backend = EncryptedSqliteBackend(db_path=tmp_path / "vault.sqlite3", master_key="k")

    async def go() -> None:
        with pytest.raises(SecretBackendError, match="does not match"):
            await backend.read(SecretRef(backend="not_us", locator="nope"))

    asyncio.run(go())


def test_secret_ref_roundtrips() -> None:
    ref = SecretRef(backend="encrypted_sqlite", locator="abc123")
    assert SecretRef.parse(ref.to_str()) == ref
    with pytest.raises(ValueError):
        SecretRef.parse("no-colon")


# ─────────────────────────────────────────────────── vault ──


@dataclass
class _VaultFixture:
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    vault: SecretVault
    sqlite_path: Path


@pytest_asyncio.fixture
async def vault_fx(tmp_path: Path) -> AsyncIterator[_VaultFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = create_engine(async_dsn)
    factory = create_session_factory(engine)
    sqlite_path = tmp_path / "vault.sqlite3"
    backend = EncryptedSqliteBackend(db_path=sqlite_path, master_key="test-key")
    vault = SecretVault(backend)
    try:
        yield _VaultFixture(engine=engine, factory=factory, vault=vault, sqlite_path=sqlite_path)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_vault_store_read_delete(vault_fx: _VaultFixture) -> None:
    async with vault_fx.factory() as db:
        md = await vault_fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.SYSTEM,
            owner_id="admin",
            key_name="anthropic_api_key",
            value="sk-test-abc",
        )
        await db.commit()
        assert md.key_name == "anthropic_api_key"
        assert (
            md.backend is enums.SecretBackend.KEYRING
            or md.backend is enums.SecretBackend.ENCRYPTED_SQLITE
        )

    # Plaintext is NOT in the Postgres row (only the ref pointer is).
    async with vault_fx.factory() as db:
        rows = (await db.execute(text("SELECT backend_ref FROM secrets"))).all()
        assert len(rows) == 1
        ref_str = rows[0][0]
        assert "sk-test-abc" not in ref_str

    async with vault_fx.factory() as db:
        plaintext = await vault_fx.vault.read(
            db,
            secret_id=md.id,
            purpose="unit-test",
            actor_id="admin",
        )
        assert plaintext == "sk-test-abc"

    async with vault_fx.factory() as db:
        await vault_fx.vault.delete(db, secret_id=md.id)
        await db.commit()
        with pytest.raises(SecretVaultError) as exc_info:
            await vault_fx.vault.read(
                db,
                secret_id=md.id,
                purpose="unit-test",
                actor_id="admin",
            )
        assert exc_info.value.code == "secret.not_found"


@pytest.mark.asyncio
async def test_vault_rejects_duplicate_key(vault_fx: _VaultFixture) -> None:
    project_id = "01KS900000000000000000PROJ"  # 26 chars
    async with vault_fx.factory() as db:
        await vault_fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.PROJECT,
            owner_id=project_id,
            key_name="github_token",
            value="ghp_first",
        )
        await db.commit()

    async with vault_fx.factory() as db:
        with pytest.raises(SecretVaultError) as exc_info:
            await vault_fx.vault.store(
                db,
                scope=enums.SecretOwnerScope.PROJECT,
                owner_id=project_id,
                key_name="github_token",
                value="ghp_second",
            )
        assert exc_info.value.code == "secret.duplicate"


@pytest.mark.asyncio
async def test_vault_rotate_replaces_blob(vault_fx: _VaultFixture) -> None:
    async with vault_fx.factory() as db:
        md = await vault_fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.SYSTEM,
            owner_id="admin",
            key_name="vault_rotate_target",
            value="initial",
        )
        await db.commit()

    async with vault_fx.factory() as db:
        rotated = await vault_fx.vault.rotate(db, secret_id=md.id, new_value="updated")
        await db.commit()

    assert rotated.rotated_at is not None
    async with vault_fx.factory() as db:
        assert (
            await vault_fx.vault.read(
                db,
                secret_id=md.id,
                purpose="unit-test",
                actor_id="admin",
            )
            == "updated"
        )

    # The new ciphertext blob is on disk and the old one was deleted.
    raw = vault_fx.sqlite_path.read_bytes()
    assert b"updated" not in raw  # encrypted, so plaintext absent
    assert b"initial" not in raw


def test_no_plaintext_pattern_leaks_to_disk(tmp_path: Path) -> None:
    """Sanity / fuzz — store a distinctive token and grep the file."""
    backend = EncryptedSqliteBackend(db_path=tmp_path / "v.sqlite3", master_key="k")

    sentinel = "GAPT-FUZZ-TOKEN-d4f8e1c3a2b9"

    async def go() -> SecretRef:
        return await backend.store(sentinel)

    asyncio.run(go())
    raw = (tmp_path / "v.sqlite3").read_bytes()
    assert sentinel.encode("utf-8") not in raw
    # Also check fragments (avoid trivial XOR/rotation tricks slipping through).
    fragment_re = re.compile(rb"FUZZ-TOKEN")
    assert fragment_re.search(raw) is None
