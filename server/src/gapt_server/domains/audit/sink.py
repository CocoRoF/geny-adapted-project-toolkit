"""AuditSink protocol + async-batched Postgres implementation.

`PostgresAuditSink.log(...)` is non-blocking — it enqueues onto an
asyncio.Queue. A background flush task drains the queue, scrubs each
payload via `masking.scrub`, and inserts up to `max_batch_size` rows
per Postgres round-trip.

`NullAuditSink` is the default before lifespan starts the real sink
(so unit tests that don't care about audit don't have to plumb the
fixture). `InMemoryAuditSink` is what tests assert against.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from sqlalchemy import insert

from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.masking import scrub

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger(__name__)


# Stable action names — the API+UI talks in terms of these strings.
class AuditAction:
    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"
    PROJECT_ARCHIVE = "project.archive"
    WORKSPACE_CREATE = "workspace.create"
    WORKSPACE_STOP = "workspace.stop"
    WORKSPACE_DELETE = "workspace.delete"
    SECRET_CREATE = "secret.create"
    SECRET_READ = "secret.read"
    SECRET_ROTATE = "secret.rotate"
    SECRET_DELETE = "secret.delete"
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    POLICY_EVALUATE = "policy.evaluate"
    SESSION_CREATE = "session.create"


@dataclass
class AuditEvent:
    action: str
    actor_type: enums.AuditActorType
    outcome: enums.AuditOutcome
    actor_id: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    subject: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    exec_code: str | None = None
    duration_ms: int | None = None
    ts: datetime | None = None  # filled in on flush if absent


class AuditSink(Protocol):
    async def log(self, event: AuditEvent) -> None: ...

    async def aclose(self) -> None: ...


# ─────────────────────────────────────────────────────────────── null ──


class NullAuditSink:
    """No-op sink — useful for tests that don't want to assert audit
    state but still need the dependency to be satisfied."""

    async def log(self, event: AuditEvent) -> None:
        return None

    async def aclose(self) -> None:
        return None


# ─────────────────────────────────────────────────────── in-memory ──


class InMemoryAuditSink:
    """Records events in a list. The order is *insertion order* —
    callers that need timestamp order should sort by `event.ts`."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = asyncio.Lock()

    async def log(self, event: AuditEvent) -> None:
        async with self._lock:
            if event.ts is None:
                event.ts = datetime.now(tz=UTC)
            self._events.append(event)

    async def aclose(self) -> None:
        return None

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)


# ──────────────────────────────────────────────────────── postgres ──


class PostgresAuditSink:
    """Async-batched audit sink.

    Lifecycle:
    - `__init__` only stores config.
    - `start()` spawns the flush task.
    - `log()` is fire-and-forget (puts on the in-process queue).
    - `aclose()` drains the queue and joins the flush task.
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        flush_interval_s: float = 0.5,
        max_batch_size: int = 200,
        max_queue_size: int = 10_000,
    ) -> None:
        self._engine = engine
        self._flush_interval = flush_interval_s
        self._max_batch = max_batch_size
        self._queue: asyncio.Queue[AuditEvent] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="gapt.audit.flush")

    async def log(self, event: AuditEvent) -> None:
        if event.ts is None:
            event.ts = datetime.now(tz=UTC)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop the event but log a warning — never block the caller.
            logger.warning(
                "audit.queue.full",
                action=event.action,
                actor_type=event.actor_type.value,
            )

    async def aclose(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            batch = await self._collect_batch()
            if not batch:
                continue
            try:
                await self._flush(batch)
            except Exception:
                logger.exception("audit.flush.failed", batch_size=len(batch))

    async def _collect_batch(self) -> list[AuditEvent]:
        batch: list[AuditEvent] = []
        try:
            first = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval)
            batch.append(first)
        except TimeoutError:
            return batch
        while len(batch) < self._max_batch:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush(self, batch: list[AuditEvent]) -> None:
        rows: list[Mapping[str, Any]] = [
            {
                "id": new_ulid(),
                "ts": event.ts,
                "actor_type": event.actor_type.value,
                "actor_id": event.actor_id,
                "scope": scrub(event.scope),
                "action": event.action,
                "subject": scrub(event.subject),
                "outcome": event.outcome.value,
                "duration_ms": event.duration_ms,
                "exec_code": event.exec_code,
                "payload": scrub(event.payload),
            }
            for event in batch
        ]
        async with self._engine.begin() as conn:
            await conn.execute(insert(models.AuditEvent), rows)
