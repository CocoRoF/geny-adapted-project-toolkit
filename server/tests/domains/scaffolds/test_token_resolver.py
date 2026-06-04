"""Phase N.2.1 — vault-backed GitHub token resolver."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio

from gapt_server.db import create_engine, create_session_factory, enums
from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode
from gapt_server.domains.scaffolds.token_resolver import (
    GITHUB_TOKEN_KEY_NAME,
    resolve_github_token,
)
from gapt_server.domains.secrets.backend import EncryptedSqliteBackend
from gapt_server.domains.secrets.vault import SecretVault
from tests._helpers.db_guard import assert_safe_to_reset

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

SERVER_ROOT = Path(__file__).resolve().parents[3]


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
class _Fx:
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    vault: SecretVault


@pytest_asyncio.fixture
async def fx(tmp_path: Path) -> AsyncIterator[_Fx]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(async_dsn)
    factory = create_session_factory(engine)
    backend = EncryptedSqliteBackend(db_path=tmp_path / "vault.sqlite3", master_key="t")
    vault = SecretVault(backend)
    try:
        yield _Fx(engine=engine, factory=factory, vault=vault)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolves_from_vault_when_github_token_secret_exists(fx: _Fx) -> None:
    async with fx.factory() as db:
        await fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.SYSTEM,
            owner_id="admin",
            key_name=GITHUB_TOKEN_KEY_NAME,
            value="ghp_thisIsTheRealToken",
        )
        await db.commit()

    async with fx.factory() as db:
        token = await resolve_github_token(
            db=db, vault=fx.vault, actor_id="admin"
        )
    assert token == "ghp_thisIsTheRealToken"


@pytest.mark.asyncio
async def test_falls_back_to_host_token_when_vault_is_empty(fx: _Fx) -> None:
    async with fx.factory() as db:
        token = await resolve_github_token(
            db=db, vault=fx.vault, actor_id="admin", fallback="ghp_hostFallback"
        )
    assert token == "ghp_hostFallback"


@pytest.mark.asyncio
async def test_raises_token_missing_when_neither_vault_nor_fallback(fx: _Fx) -> None:
    async with fx.factory() as db:
        with pytest.raises(ScaffoldError) as exc:
            await resolve_github_token(
                db=db, vault=fx.vault, actor_id="admin", fallback=None
            )
    assert exc.value.code is ScaffoldErrorCode.TOKEN_MISSING


@pytest.mark.asyncio
async def test_vault_takes_priority_over_fallback(fx: _Fx) -> None:
    """Operator's deliberate Settings entry must beat the
    implicit `gh auth token` host fallback."""
    async with fx.factory() as db:
        await fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.SYSTEM,
            owner_id="admin",
            key_name=GITHUB_TOKEN_KEY_NAME,
            value="ghp_explicit_admin_choice",
        )
        await db.commit()

    async with fx.factory() as db:
        token = await resolve_github_token(
            db=db,
            vault=fx.vault,
            actor_id="admin",
            fallback="ghp_should_be_ignored",
        )
    assert token == "ghp_explicit_admin_choice"


@pytest.mark.asyncio
async def test_ignores_non_github_keys(fx: _Fx) -> None:
    """A different system-scoped secret (anthropic_api_key, etc.) must
    not be returned by the github_token resolver."""
    async with fx.factory() as db:
        await fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.SYSTEM,
            owner_id="admin",
            key_name="anthropic_api_key",
            value="sk-ant-not-a-github-token",
        )
        await db.commit()

    async with fx.factory() as db:
        with pytest.raises(ScaffoldError) as exc:
            await resolve_github_token(
                db=db, vault=fx.vault, actor_id="admin", fallback=None
            )
    assert exc.value.code is ScaffoldErrorCode.TOKEN_MISSING
