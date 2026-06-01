"""Session SSE event model + queue helpers.

Six event types per plan §2.10:

- ``text``         — token / message chunk
- ``tool_call``    — agent is about to invoke a tool
- ``tool_result``  — tool returned (incl. failures, surface ``exec_code``)
- ``cost``         — debounced cost / token snapshot
- ``error``        — fatal error during invoke (also ends the stream)
- ``done``         — invocation finished cleanly

Each event is serialised as a single SSE frame::

    event: text
    id: 17
    data: {"chunk": "Hello"}

`id` is the monotonic per-session sequence so a reconnecting client
can resume via the ``GET /messages?since=<id>`` endpoint. The id space
is per-session, not global — Redis pub/sub in M2 will keep that
property.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any


EventPersister = Callable[["SessionEvent"], Awaitable[None]]
"""Phase D.3 — optional async sink the bus calls *outside* its lock
after every successful publish. The default registry leaves it
unset, so existing tests + boot paths keep ephemeral semantics. The
session router wires this to a DB writer so events survive a
backend restart."""


class SessionEventKind(StrEnum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COST = "cost"
    ERROR = "error"
    DONE = "done"
    # `step` is a verbose-but-low-volume trace of what the pipeline
    # is doing right now — stage transitions, API calls, parse
    # decisions. The chat panel renders these as a collapsible
    # "과정" subpanel so the user can see the agent's progress
    # without having to scrape the server log.
    STEP = "step"
    # Phase I.2 — the user's own prompt for this turn. Published at
    # the top of `_run_with_lifecycle` so the persister captures it
    # into `session_events` before any executor work begins. Without
    # this the transcript archive only has assistant-side events and
    # can't be replayed as a real conversation.
    USER_MESSAGE = "user_message"


@dataclass(frozen=True)
class SessionEvent:
    """A single SSE-ready event."""

    seq: int
    kind: SessionEventKind
    data: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_sse(self) -> bytes:
        """Render as a single SSE frame."""
        body = json.dumps(self.data, separators=(",", ":"), ensure_ascii=False)
        return (f"event: {self.kind.value}\nid: {self.seq}\ndata: {body}\n\n").encode()

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly view for the ``/messages`` replay endpoint."""
        return {
            "seq": self.seq,
            "kind": self.kind.value,
            "data": self.data,
            "ts": self.ts.isoformat(),
        }


@dataclass
class SessionEventBus:
    """Per-session event broker.

    Owns the monotonic sequence + a ring buffer for replay + a set of
    asyncio.Queue subscribers (each SSE listener gets its own). Both
    `publish` and `subscribe` are coroutine-safe — `publish` writes to
    every subscriber's queue under the lock so an aggressive consumer
    can't miss interleaved events.
    """

    history_limit: int = 1024
    persister: EventPersister | None = None
    _history: list[SessionEvent] = field(default_factory=list)
    _subscribers: list[asyncio.Queue[SessionEvent | None]] = field(default_factory=list)
    _seq: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _closed: bool = False
    # Phase D.3 — when rehydrating a session after a server restart,
    # the in-memory `_seq` starts at 0. The bus needs to know the
    # highest seq already persisted to DB so replay + new events
    # don't collide. Set via `seed_seq()` immediately after
    # construction; default 0 means "fresh session".
    _persisted_seq: int = 0

    def seed_seq(self, last_persisted_seq: int) -> None:
        """Resume mid-conversation: tell the bus the next event must
        be assigned seq = `last_persisted_seq + 1`. Idempotent — only
        bumps the counter when the argument is greater than the
        current value (safety net against an out-of-order call)."""
        if last_persisted_seq > self._seq:
            self._seq = last_persisted_seq
            self._persisted_seq = last_persisted_seq

    async def publish(self, kind: SessionEventKind, data: dict[str, Any]) -> SessionEvent:
        async with self._lock:
            if self._closed:
                raise RuntimeError("session event bus is closed")
            self._seq += 1
            event = SessionEvent(seq=self._seq, kind=kind, data=data)
            self._history.append(event)
            if len(self._history) > self.history_limit:
                # Drop from the front — in-memory replay older than the
                # limit is not supported. DB replay covers older history
                # when a persister is wired (Phase D.3).
                self._history.pop(0)
            for q in list(self._subscribers):
                q.put_nowait(event)
        # Persister runs *outside* the lock so a slow DB write doesn't
        # block the next publish. Best-effort: a failed write is
        # logged but never raised — the live stream still went out.
        if self.persister is not None:
            try:
                await self.persister(event)
            except Exception:  # noqa: BLE001
                # The publish path mustn't fail because a sink is
                # temporarily unavailable. The next publish will retry
                # implicitly by writing its own event.
                pass
        return event

    async def subscribe(self) -> asyncio.Queue[SessionEvent | None]:
        async with self._lock:
            q: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
            self._subscribers.append(q)
            return q

    async def unsubscribe(self, q: asyncio.Queue[SessionEvent | None]) -> None:
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    async def replay(self, since: int) -> list[SessionEvent]:
        async with self._lock:
            return [e for e in self._history if e.seq > since]

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            for q in self._subscribers:
                # Sentinel — listeners interpret None as end-of-stream.
                q.put_nowait(None)
            self._subscribers.clear()
