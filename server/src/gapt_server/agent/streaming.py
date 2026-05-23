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
from enum import StrEnum
from typing import Any


class SessionEventKind(StrEnum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COST = "cost"
    ERROR = "error"
    DONE = "done"


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
    _history: list[SessionEvent] = field(default_factory=list)
    _subscribers: list[asyncio.Queue[SessionEvent | None]] = field(default_factory=list)
    _seq: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _closed: bool = False

    async def publish(self, kind: SessionEventKind, data: dict[str, Any]) -> SessionEvent:
        async with self._lock:
            if self._closed:
                raise RuntimeError("session event bus is closed")
            self._seq += 1
            event = SessionEvent(seq=self._seq, kind=kind, data=data)
            self._history.append(event)
            if len(self._history) > self.history_limit:
                # Drop from the front — replay older than the limit is
                # not supported.
                self._history.pop(0)
            for q in list(self._subscribers):
                q.put_nowait(event)
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
