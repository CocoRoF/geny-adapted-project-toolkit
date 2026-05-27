"""Presence-driven fan-out for live container telemetry.

A single background task samples `docker stats` on a fixed cadence
and pushes the result to every active SSE subscriber's queue. The
task is *started* on the first connect and *cancelled* on the last
disconnect — so when no browser tab is looking at the Performance
page, we consume zero CPU and place zero load on the docker daemon.

Why a broadcaster instead of "one sampling loop per connection":
when the operator opens 3 tabs at the same workspace, naive per-
connection sampling would 3× the docker-daemon load. Fan-out from a
single source keeps the work O(1) in the number of viewers.

Per-subscriber queues are bounded + drop-oldest under back-pressure
so a slow client never wedges the broadcaster. The first payload
on a fresh subscribe is the cached sample (no wait for the next
tick) so the dashboard renders instantly.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from gapt_server.domains.performance.sampler import (
    ContainerSample,
    ContainerSampler,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger(__name__)


# Per-subscriber queue depth. Two ticks of slack lets a momentarily
# stalled connection catch up; beyond that we drop the oldest
# pending payload so the queue never bloats and the broadcaster
# never blocks on a slow client.
_QUEUE_DEPTH = 2

# Sampling cadence. The expensive part — `docker stats` deltas — is
# already ~1 s per container; sampling every 2 s leaves headroom on
# medium-sized hosts (~20 containers) while keeping the UI feeling
# live. The sampler's TTL cache is set to the same window so cache
# misses on adjacent endpoints stay aligned.
_TICK_S = 2.0


# Caller-supplied async hook that produces the *latest* payload
# (joined sample + DB enrichment). The broadcaster doesn't know
# about the DB; the router wires this up.
PayloadBuilder = "Callable[[list[ContainerSample]], Awaitable[dict]]"


@dataclass(eq=False)
class _Subscriber:
    """Identity-only equality so instances can live in a `set()`
    even with a mutable `dropped` counter. Each subscribe() call
    gets its own _Subscriber object; we never compare by value."""

    queue: asyncio.Queue[bytes]
    # Per-subscriber drop counter — surfaced as the SSE `event: drop`
    # message so the client can show "had to skip frames" warnings
    # if it actually matters. We don't yet, but it's cheap to keep.
    dropped: int = 0


class PerformanceBroadcaster:
    def __init__(
        self,
        sampler: ContainerSampler,
        *,
        payload_builder: PayloadBuilder | None = None,
        tick_s: float = _TICK_S,
    ) -> None:
        self._sampler = sampler
        self._build_payload: PayloadBuilder | None = payload_builder
        self._tick_s = tick_s
        self._subscribers: set[_Subscriber] = set()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        # Latest payload — sent to fresh subscribers immediately so
        # the dashboard renders without waiting for the next tick.
        # `None` only before the very first sample of the process.
        self._latest: bytes | None = None

    def set_payload_builder(self, fn: PayloadBuilder) -> None:
        """Wire the DB-join function in at app start — keeps the
        broadcaster decoupled from the router's DB session
        plumbing."""
        self._build_payload = fn

    async def force_push(self) -> None:
        """Sample + build + fan out a frame RIGHT NOW, then update
        the replay cache. Lets the orphan-cleanup endpoint surface
        its effect immediately instead of waiting up to `tick_s` (2s)
        for the next regular tick. Safe to call even when there are
        no subscribers — the replay cache is updated for the next
        subscribe."""
        if self._build_payload is None:
            return
        try:
            samples = await self._sampler.sample_all()
            payload = await self._build_payload(samples)
            frame = _encode_sse("stats", payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("performance.broadcaster.force_push_failed", error=str(exc))
            return
        self._latest = frame
        for sub in list(self._subscribers):
            self._enqueue(sub, frame)

    async def subscribe(self) -> AsyncIterator[bytes]:
        """Yields raw SSE-formatted byte frames. Cancellation /
        connection-close is expected to surface as `GeneratorExit`
        / `CancelledError`; we tear the queue down then."""
        sub = _Subscriber(queue=asyncio.Queue(maxsize=_QUEUE_DEPTH))
        async with self._lock:
            self._subscribers.add(sub)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(
                    self._run(), name="performance-broadcaster"
                )
                logger.info("performance.broadcaster.started")
        try:
            # Replay-cache: hand the newcomer the most recent payload
            # right away. Eliminates a 2 s blank state on first open.
            if self._latest is not None:
                yield self._latest
            while True:
                frame = await sub.queue.get()
                yield frame
        finally:
            async with self._lock:
                self._subscribers.discard(sub)
                if not self._subscribers and self._task is not None:
                    self._task.cancel()
                    self._task = None
                    logger.info("performance.broadcaster.stopped")

    async def _run(self) -> None:
        """Sample → build payload → fan out → sleep. Runs until
        cancelled by the last-unsubscribe path."""
        try:
            while True:
                started = asyncio.get_event_loop().time()
                try:
                    samples = await self._sampler.sample_all()
                    payload = (
                        await self._build_payload(samples)
                        if self._build_payload is not None
                        else _bare_payload(samples)
                    )
                    frame = _encode_sse("stats", payload)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("performance.broadcaster.tick_failed", error=str(exc))
                    frame = _encode_sse("error", {"reason": str(exc)})
                self._latest = frame
                for sub in list(self._subscribers):
                    self._enqueue(sub, frame)
                # Maintain the tick cadence regardless of how long
                # this iteration took. If sampling slipped past the
                # tick budget (busy host) we just sleep ~0 and
                # iterate again.
                elapsed = asyncio.get_event_loop().time() - started
                await asyncio.sleep(max(self._tick_s - elapsed, 0.0))
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _enqueue(sub: _Subscriber, frame: bytes) -> None:
        try:
            sub.queue.put_nowait(frame)
            return
        except asyncio.QueueFull:
            pass
        # Drop the oldest pending frame so the newest one always
        # wins. Better UX than blocking the broadcaster.
        try:
            sub.queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            sub.queue.put_nowait(frame)
            sub.dropped += 1
        except asyncio.QueueFull:
            sub.dropped += 1


# ─────────────────────────────────────────────────── encoding ──


def _encode_sse(event: str, data: object) -> bytes:
    body = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


def _bare_payload(samples: list[ContainerSample]) -> dict:
    """Fallback when no DB-aware payload builder is wired in. Lets
    the broadcaster still tick during early app boot."""
    return {
        "samples": [
            {
                "id": s.summary.id,
                "name": s.summary.name,
                "category": s.summary.category.value,
                "status": s.summary.status,
                "cpu_pct": s.stats.cpu_pct if s.stats else 0,
                "mem_bytes": s.stats.mem_bytes if s.stats else 0,
            }
            for s in samples
        ],
        "total_containers": len(samples),
    }
