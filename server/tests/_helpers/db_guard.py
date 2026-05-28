"""Hard guard against running destructive test setup against the
operator's live database.

Background — 2026-05-28: every DSN-gated test (`tests/workspaces/
test_routes.py`, `tests/cost/test_routes.py`, etc.) ships its own
local `_reset_and_upgrade(sync_dsn)` which runs

    DROP SCHEMA public CASCADE;
    CREATE SCHEMA public;

then alembic-upgrades the empty schema. The convention is that the
caller exports `GAPT_TEST_POSTGRES_DSN` pointing at a *separate*
test database (e.g. `gapt_test`), but nothing in the test fixtures
verified that. When the env var accidentally pointed at the live
`gapt` database, every test invocation silently wiped the operator's
projects / environments / secrets / Cloudflare config.

This module provides a single `assert_safe_to_reset(sync_dsn)` that
*every* `_reset_and_upgrade` callsite invokes before issuing the
DROP. The check is conservative — it requires an explicit `_test`
marker in the database name. False positives (refusing a legitimate
test DB called `mydb`) are fine: rename it. False negatives (letting
through a live DB) are catastrophic and the whole point of this
guard.
"""

from __future__ import annotations

from urllib.parse import urlparse


class UnsafeTestDsnError(RuntimeError):
    """Raised by `assert_safe_to_reset` when the DSN doesn't carry
    a clear `_test` marker in the database name."""


def assert_safe_to_reset(sync_dsn: str) -> None:
    """Refuse to drop a database whose name doesn't visibly mark
    it as a test target.

    Accepted database names:
      - ends in `_test` (e.g. `gapt_test`, `myapp_test`)
      - starts with `test_` (e.g. `test_gapt`)
      - equals `test` exactly

    Anything else raises so the test runner errors out instead of
    silently wiping production data.

    The check intentionally looks at the *path* component of the
    DSN (the database name) rather than the host — running a test
    DB on the same Postgres instance as production is fine; what
    matters is that we're operating on a different database.
    """
    parsed = urlparse(sync_dsn)
    db_name = parsed.path.lstrip("/").strip()
    if not db_name:
        raise UnsafeTestDsnError(
            "DROP SCHEMA refused: DSN has no database name. "
            f"DSN={sync_dsn!r}."
        )
    looks_test = (
        db_name.endswith("_test")
        or db_name.startswith("test_")
        or db_name == "test"
    )
    if not looks_test:
        raise UnsafeTestDsnError(
            "DROP SCHEMA refused: the test runner's DSN points at "
            f"database {db_name!r}, which doesn't look like a test "
            "database (must end in '_test', start with 'test_', or "
            "equal 'test'). Create a dedicated test database — e.g. "
            "`CREATE DATABASE gapt_test;` — and re-export "
            "GAPT_TEST_POSTGRES_DSN to point at it. This guard exists "
            "because on 2026-05-28 the live `gapt` database was "
            "wiped by tests pointed at it. See "
            "`tests/_helpers/db_guard.py` for the full story."
        )
