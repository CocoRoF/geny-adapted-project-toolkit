"""CredentialBundle builder — claude_code_cli always present,
SDK providers only when secret_ref is mapped, vault read emits audit."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import pytest_asyncio
from tests._helpers.db_guard import assert_safe_to_reset

from gapt_server.agent.credentials import (
    SecretRefMap,
    build_claude_code_cli_creds,
    build_for_session,
    claude_binary,
)
from gapt_server.db import create_engine, create_session_factory, enums
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.secrets.backend import EncryptedSqliteBackend
from gapt_server.domains.secrets.vault import SecretVault

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

SERVER_ROOT = Path(__file__).resolve().parents[2]


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


# ─────────────────────────────────────────────── hermetic tests ──


def test_claude_binary_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_BIN", "/env/claude")
    assert claude_binary(override="/explicit/claude") == "/explicit/claude"


def test_claude_binary_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_BIN", "/env/claude")
    assert claude_binary() == "/env/claude"


def test_claude_binary_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(FileNotFoundError, match=r"claude.*PATH"):
        claude_binary()


def test_claude_code_cli_creds_carries_extras() -> None:
    creds = build_claude_code_cli_creds(
        binary_path="/usr/local/bin/claude",
        workspace_root="/workspace",
        settings_path='{"permissions":{"allow":["mcp__gapt"]}}',
        mcp_config={"mcpServers": {"gapt": {"command": "uv"}}},
        timeout_s=42.0,
        max_budget_usd=2.5,
    )
    assert creds.binary_path == "/usr/local/bin/claude"
    assert creds.extras["bare_mode"] is True
    assert creds.extras["workspace_root"] == "/workspace"
    assert creds.extras["timeout_s"] == 42.0
    assert creds.extras["max_budget_usd"] == 2.5
    assert creds.extras["settings_path"].startswith('{"permissions"')
    assert "mcpServers" in creds.extras["mcp_config"]


def test_claude_code_cli_creds_omits_unset_optionals() -> None:
    creds = build_claude_code_cli_creds(
        binary_path="/usr/local/bin/claude",
        max_budget_usd=None,
    )
    # The optional keys are absent when not provided.
    assert "max_budget_usd" not in creds.extras
    assert "settings_path" not in creds.extras
    assert "mcp_config" not in creds.extras
    assert "workspace_root" not in creds.extras


def test_claude_code_cli_creds_default_omits_budget_flag() -> None:
    """Phase N.3 — the spawned ``claude`` CLI no longer receives
    ``--max-budget-usd`` unless an explicit value is passed in.
    Budget enforcement is GAPT-side now (see
    ``routers/sessions.invoke_session``). When the agent sees a
    budget flag, the CLI's "your budget is X" metadata leaks into
    the LLM's prompt context and the model starts producing
    "남은 예산이 빠듯하니..." meta-cognitive chatter mid-task. The
    default-None behaviour is the fix.

    Tests and ops paths that need the CLI-side cap still can pass
    ``max_budget_usd=<float>`` explicitly — this test pins only the
    default."""
    creds = build_claude_code_cli_creds(binary_path="/usr/local/bin/claude")
    assert "max_budget_usd" not in creds.extras


# ─────────────────────────────────────── integration (Postgres) ──


@dataclass
class _CredsFixture:
    engine: AsyncEngine
    vault: SecretVault
    audit: InMemoryAuditSink


@pytest_asyncio.fixture
async def creds_fx(tmp_path: Path) -> AsyncIterator[_CredsFixture]:
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(async_dsn)
    audit = InMemoryAuditSink()
    backend = EncryptedSqliteBackend(db_path=tmp_path / "vault.sqlite", master_key="k")
    vault = SecretVault(backend, audit_sink=audit)
    try:
        yield _CredsFixture(engine=engine, vault=vault, audit=audit)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bundle_includes_claude_code_cli_always(
    creds_fx: _CredsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    factory = create_session_factory(creds_fx.engine)
    async with factory() as db:
        bundle = await build_for_session(
            db=db,
            vault=creds_fx.vault,
            actor_id="01KS90000000000000000USER",
        )
    assert "claude_code_cli" in bundle.by_provider
    # SDK providers are absent when no secret refs are mapped.
    assert "anthropic" not in bundle.by_provider
    assert "openai" not in bundle.by_provider


@pytest.mark.asyncio
async def test_bundle_includes_sdk_creds_when_mapped(
    creds_fx: _CredsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    factory = create_session_factory(creds_fx.engine)
    async with factory() as db:
        anthropic_md = await creds_fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.PROJECT,
            owner_id="01KS90000000000000000PROJ",
            key_name="anthropic_api_key",
            value="sk-test-anthropic",
        )
        openai_md = await creds_fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.PROJECT,
            owner_id="01KS90000000000000000PROJ",
            key_name="openai_api_key",
            value="sk-test-openai",
        )
        await db.commit()

    async with factory() as db:
        bundle = await build_for_session(
            db=db,
            vault=creds_fx.vault,
            actor_id="01KS90000000000000000USER",
            secret_refs=SecretRefMap(anthropic=anthropic_md.id, openai=openai_md.id),
        )
    assert bundle.by_provider["anthropic"].api_key == "sk-test-anthropic"
    assert bundle.by_provider["openai"].api_key == "sk-test-openai"
    # google / vllm stay absent because they weren't mapped.
    assert "google" not in bundle.by_provider
    assert "vllm" not in bundle.by_provider

    # Vault read emitted 2 secret.read audit events with the actor id.
    reads = [e for e in creds_fx.audit.events if e.action == "secret.read"]
    assert len(reads) == 2
    assert all(e.actor_id == "01KS90000000000000000USER" for e in reads)
    assert {e.payload["purpose"] for e in reads} == {
        "agent_session.anthropic",
        "agent_session.openai",
    }


@pytest.mark.asyncio
async def test_system_scoped_anthropic_key_flows_into_claude_cli(
    creds_fx: _CredsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the project doesn't supply secret_refs, a system-scoped
    `anthropic_api_key` should fall through to both the SDK provider
    *and* the spawned claude_code_cli (overriding any host env)."""
    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-process-env")
    factory = create_session_factory(creds_fx.engine)

    async with factory() as db:
        await creds_fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.SYSTEM,
            owner_id="admin",
            key_name="anthropic_api_key",
            value="sk-from-admin-vault",
        )
        await creds_fx.vault.store(
            db,
            scope=enums.SecretOwnerScope.SYSTEM,
            owner_id="admin",
            key_name="openai_api_key",
            value="sk-openai-from-vault",
        )
        await db.commit()

    async with factory() as db:
        bundle = await build_for_session(
            db=db,
            vault=creds_fx.vault,
            actor_id="admin",
        )

    # claude_code_cli prefers the vault key over the host env.
    assert bundle.by_provider["claude_code_cli"].api_key == "sk-from-admin-vault"
    # SDK anthropic + openai providers are auto-included from the
    # system-scoped vault keys.
    assert bundle.by_provider["anthropic"].api_key == "sk-from-admin-vault"
    assert bundle.by_provider["openai"].api_key == "sk-openai-from-vault"


@pytest.mark.asyncio
async def test_missing_admin_secret_falls_back_to_empty_key(
    creds_fx: _CredsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No system-scoped secret means claude_code_cli has no API key
    and the SDK anthropic provider stays out of the bundle entirely."""
    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    factory = create_session_factory(creds_fx.engine)

    async with factory() as db:
        bundle = await build_for_session(
            db=db,
            vault=creds_fx.vault,
            actor_id="admin",
        )

    # No secret stored — claude_code_cli falls back to "".
    assert bundle.by_provider["claude_code_cli"].api_key == ""
    assert "anthropic" not in bundle.by_provider
