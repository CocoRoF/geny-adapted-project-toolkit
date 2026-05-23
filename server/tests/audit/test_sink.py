"""PostgresAuditSink — batched flush + secret masking + ordering."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import text

from gapt_server.db import create_engine, enums
from gapt_server.domains.audit.sink import (
    AuditEvent,
    InMemoryAuditSink,
    NullAuditSink,
    PostgresAuditSink,
)

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


@pytest.mark.asyncio
async def test_null_sink_is_noop() -> None:
    sink = NullAuditSink()
    await sink.log(
        AuditEvent(
            action="noop",
            actor_type=enums.AuditActorType.SYSTEM,
            outcome=enums.AuditOutcome.OK,
        )
    )
    await sink.aclose()


@pytest.mark.asyncio
async def test_in_memory_sink_records_events() -> None:
    sink = InMemoryAuditSink()
    await sink.log(
        AuditEvent(
            action="t.a",
            actor_type=enums.AuditActorType.USER,
            outcome=enums.AuditOutcome.OK,
        )
    )
    await sink.log(
        AuditEvent(
            action="t.b",
            actor_type=enums.AuditActorType.SYSTEM,
            outcome=enums.AuditOutcome.DENIED,
        )
    )
    assert [e.action for e in sink.events] == ["t.a", "t.b"]
    assert all(e.ts is not None for e in sink.events)


@pytest_asyncio.fixture
async def pg_engine():
    sync_dsn = _require_dsn()
    _reset_and_upgrade(sync_dsn)
    async_dsn = sync_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(async_dsn)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_sink_flushes_batch(pg_engine) -> None:
    sink = PostgresAuditSink(engine=pg_engine, flush_interval_s=0.05, max_batch_size=50)
    sink.start()

    n = 200
    for i in range(n):
        await sink.log(
            AuditEvent(
                action=f"burst.{i:04d}",
                actor_type=enums.AuditActorType.SYSTEM,
                outcome=enums.AuditOutcome.OK,
                scope={"i": i},
            )
        )

    await sink.aclose()

    async with pg_engine.begin() as conn:
        rows = (
            await conn.execute(text("SELECT action, scope FROM audit_events ORDER BY action"))
        ).all()
    assert len(rows) == n
    # ULID PK preserves insertion order; action ordering is also lex sorted
    # because of the zero-padded suffix, so the round-trip is straightforward
    # to assert.
    assert rows[0][0] == "burst.0000"
    assert rows[-1][0] == "burst.0199"


@pytest.mark.asyncio
async def test_postgres_sink_scrubs_secrets(pg_engine) -> None:
    sink = PostgresAuditSink(engine=pg_engine, flush_interval_s=0.05)
    sink.start()
    await sink.log(
        AuditEvent(
            action="probe.scrub",
            actor_type=enums.AuditActorType.USER,
            outcome=enums.AuditOutcome.OK,
            payload={"args": "sk-ant-abcdefghijklmnopqrstuvwxyz1234567890"},
        )
    )
    await sink.aclose()

    async with pg_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT payload FROM audit_events WHERE action = 'probe.scrub'")
            )
        ).one()
    payload = row[0]
    assert "sk-ant-" not in str(payload)
    assert "[redacted:anthropic_api_key]" in str(payload)


@pytest.mark.asyncio
async def test_postgres_sink_drops_on_queue_full(pg_engine) -> None:
    # Sink with a tiny queue. We never start the flush task, so logs
    # pile up and the third one is dropped (with a warning).
    sink = PostgresAuditSink(engine=pg_engine, max_queue_size=2)
    await sink.log(
        AuditEvent(
            action="a",
            actor_type=enums.AuditActorType.SYSTEM,
            outcome=enums.AuditOutcome.OK,
        )
    )
    await sink.log(
        AuditEvent(
            action="b",
            actor_type=enums.AuditActorType.SYSTEM,
            outcome=enums.AuditOutcome.OK,
        )
    )
    # Third put should be dropped (non-blocking), not raise.
    await sink.log(
        AuditEvent(
            action="c",
            actor_type=enums.AuditActorType.SYSTEM,
            outcome=enums.AuditOutcome.OK,
        )
    )
    # The drain task was never started; tearing down is still clean.
    await sink.aclose()
    # Hush asyncio about the unconsumed queue items.
    while not sink._queue.empty():
        sink._queue.get_nowait()
    await asyncio.sleep(0)
