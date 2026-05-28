"""Migration round-trip test — gates the M1-E1 Cycle 1.1 DoD.

Runs only when `GAPT_TEST_POSTGRES_DSN` is set (CI provides it via
service; local devs run `docker run -d --rm -e POSTGRES_PASSWORD=gapt …
postgres:16-alpine` and export the DSN before pytest).

The test:
1. Drops the public schema clean.
2. `alembic upgrade head` from scratch.
3. Counts tables + enums.
4. `alembic downgrade base`.
5. Asserts only `alembic_version` survives + zero enums remain.
6. Re-`upgrade head` to prove idempotency.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from tests._helpers.db_guard import assert_safe_to_reset

SERVER_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "admin_agent_prefs",
        "agent_sessions",
        "audit_events",
        "deploy_runs",
        "environments",
        "projects",
        "sandboxes",
        "secrets",
        "workspaces",
    }
)

EXPECTED_ENUMS: frozenset[str] = frozenset(
    {
        "agent_session_status_enum",
        "audit_actor_type_enum",
        "audit_outcome_enum",
        "deploy_target_kind_enum",
        "git_provider_enum",
        "sandbox_status_enum",
        "secret_backend_enum",
        "secret_owner_scope_enum",
        "workspace_status_enum",
    }
)


def _dsn() -> str:
    dsn = os.environ.get("GAPT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip(
            "GAPT_TEST_POSTGRES_DSN unset — run with: "
            "GAPT_TEST_POSTGRES_DSN=postgresql://gapt:gapt_dev_only@localhost:35432/gapt_test pytest tests/db"
        )
    return dsn


def _alembic(command: list[str], *, dsn: str) -> None:
    env = os.environ.copy()
    env["GAPT_POSTGRES_DSN"] = (
        dsn
        if not dsn.startswith("postgresql+asyncpg")
        else dsn.replace("postgresql+asyncpg", "postgresql+psycopg", 1)
    )
    result = subprocess.run(
        ["uv", "run", "alembic", *command],
        cwd=SERVER_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(command)} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def _reset_schema(dsn: str) -> None:
    assert_safe_to_reset(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT ALL ON SCHEMA public TO PUBLIC")


def _list_tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name != 'alembic_version'"
        )
        return {row[0] for row in cur.fetchall()}


def _list_enums(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.typname FROM pg_type t JOIN pg_namespace n "
            "ON t.typnamespace = n.oid WHERE n.nspname='public' AND t.typtype='e'"
        )
        return {row[0] for row in cur.fetchall()}


def test_upgrade_downgrade_clean() -> None:
    dsn = _dsn()
    sync_dsn = dsn.replace("postgresql+asyncpg", "postgresql", 1).replace(
        "postgresql+psycopg", "postgresql", 1
    )

    _reset_schema(sync_dsn)

    _alembic(["upgrade", "head"], dsn=dsn)
    assert _list_tables(sync_dsn) == EXPECTED_TABLES, (
        "upgrade head did not produce the expected control-plane tables"
    )
    assert _list_enums(sync_dsn) == EXPECTED_ENUMS, (
        "upgrade head did not produce the expected enum types"
    )

    _alembic(["downgrade", "base"], dsn=dsn)
    assert _list_tables(sync_dsn) == set(), "downgrade base left non-alembic tables behind"
    assert _list_enums(sync_dsn) == set(), "downgrade base left enum types behind"

    _alembic(["upgrade", "head"], dsn=dsn)
    assert _list_tables(sync_dsn) == EXPECTED_TABLES, "re-upgrade not idempotent"
