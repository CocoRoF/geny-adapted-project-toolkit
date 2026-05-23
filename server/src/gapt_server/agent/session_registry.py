"""In-process registry of live agent sessions.

M1 keeps everything in one server process. Each `SessionRuntime` owns
the `Pipeline` instance returned by `ProjectAwareSessionManager`, the
SSE event bus, the hook accumulator, and (when invoke is running) the
asyncio.Task that is mapping pipeline events into SSE events.

M2 swaps this for Redis pub/sub so multiple workers can serve the same
session. The shape of `SessionRuntime` stays — only `SessionRegistry`
gains a sharded lookup.

`invoke()` is the seam: it kicks off the work in a background task and
returns immediately. The Task pulls `Pipeline.run_stream(message)` and
publishes onto the bus. For tests, callers can override the runner via
`invoke_runner=`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from gapt_server.agent.streaming import (
    SessionEventBus,
    SessionEventKind,
)

if TYPE_CHECKING:
    from geny_executor import Pipeline

    from gapt_server.agent.hooks.cost_hook import CostAccumulator


logger = structlog.get_logger(__name__)


# Pluggable so tests can mock pipeline.run_stream without spawning a
# real LLM call. The default below maps PipelineEvent → SessionEvent.
SessionInvokeRunner = Callable[
    ["SessionRuntime", str],
    Awaitable[None],
]


class SessionAlreadyInvoking(RuntimeError):
    """Raised when invoke is called while a prior task is still running."""


class SessionNotFound(KeyError):
    """Raised when the registry has no entry for a session id."""


@dataclass
class SessionRuntime:
    session_id: str
    project_id: str
    workspace_id: str
    user_id: str
    pipeline: Pipeline
    accumulator: CostAccumulator
    bus: SessionEventBus = field(default_factory=SessionEventBus)
    _task: asyncio.Task[None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def invoke(self, message: str, *, runner: SessionInvokeRunner | None = None) -> None:
        """Kick off a background task that runs the pipeline against
        `message` and publishes SSE events. Returns once the task is
        scheduled. The task lifetime tracks the SSE stream lifetime —
        callers should `await` the task only via `wait_done()` (which
        also handles the cancellation path)."""
        async with self._lock:
            if self.is_running:
                raise SessionAlreadyInvoking(f"session {self.session_id} is already invoking")
            actual_runner = runner or _default_invoke_runner
            self._task = asyncio.create_task(
                _run_with_lifecycle(self, message, actual_runner),
                name=f"session-invoke-{self.session_id}",
            )

    async def wait_done(self) -> None:
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def interrupt(self) -> bool:
        """Cancel the running invoke task if any. Returns True when an
        active task was cancelled."""
        async with self._lock:
            if not self.is_running or self._task is None:
                return False
            self._task.cancel()
        return True

    async def aclose(self) -> None:
        await self.interrupt()
        await self.wait_done()
        await self.bus.close()


async def _run_with_lifecycle(
    runtime: SessionRuntime, message: str, runner: SessionInvokeRunner
) -> None:
    """Wraps the runner so we always publish a terminal `done` or
    `error` event regardless of how the task ends."""
    try:
        await runner(runtime, message)
    except asyncio.CancelledError:
        # Park the cancellation so the next await isn't auto-cancelled,
        # publish the user-visible error frame, then re-raise so the
        # task still ends with CancelledError state.
        current = asyncio.current_task()
        if current is not None:
            current.uncancel()
        await runtime.bus.publish(
            SessionEventKind.ERROR,
            {"exec_code": "exec.session.cancelled", "reason": "invoke cancelled"},
        )
        raise
    except Exception as exc:
        logger.exception("session.invoke_crashed", session_id=runtime.session_id)
        await runtime.bus.publish(
            SessionEventKind.ERROR,
            {
                "exec_code": "exec.session.crashed",
                "reason": f"{type(exc).__name__}: {exc}"[:400],
            },
        )
    else:
        await runtime.bus.publish(
            SessionEventKind.DONE,
            {"cost": runtime.accumulator.snapshot()},
        )


async def _default_invoke_runner(runtime: SessionRuntime, message: str) -> None:
    """Drives `Pipeline.run_stream(message)` and maps each
    `PipelineEvent` onto a SessionEvent. Stage 10 (`tool`) emits
    fine-grained events the hook runner has already counted into
    accumulator — we still publish a `tool_call` / `tool_result` pair
    so the UI sees the agent's tool use without scraping logs."""
    async for ev in runtime.pipeline.run_stream(message):
        kind, payload = _map_pipeline_event(ev)
        if kind is None:
            continue
        await runtime.bus.publish(kind, payload)


def _map_pipeline_event(  # noqa: PLR0911 — 7-way taxonomy reads cleaner as straight returns
    event: object,
) -> tuple[SessionEventKind | None, dict[str, object]]:
    """Map a `geny_executor.PipelineEvent` → (kind, data). Unknown
    event types map to None so the stream stays compact."""
    event_type = getattr(event, "type", "")
    data: dict[str, object] = dict(getattr(event, "data", {}) or {})
    if event_type in {"pipeline.start", "stage.enter", "stage.exit", "stage.bypass"}:
        return None, {}
    if event_type == "pipeline.complete":
        # Suppress — `_run_with_lifecycle` already issues the done event.
        return None, {}
    if event_type == "pipeline.error":
        return SessionEventKind.ERROR, data
    if event_type.endswith(".chunk") or event_type == "text":
        return SessionEventKind.TEXT, data
    if "tool" in event_type and "result" in event_type:
        return SessionEventKind.TOOL_RESULT, data
    if "tool" in event_type and ("call" in event_type or "invoke" in event_type):
        return SessionEventKind.TOOL_CALL, data
    return None, {}


@dataclass
class SessionRegistry:
    """Stateless-ish lookup by session_id. Lock guards the dict; each
    runtime owns its own per-session lock."""

    _entries: dict[str, SessionRuntime] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def register(self, runtime: SessionRuntime) -> None:
        async with self._lock:
            self._entries[runtime.session_id] = runtime

    async def get(self, session_id: str) -> SessionRuntime:
        async with self._lock:
            try:
                return self._entries[session_id]
            except KeyError as exc:
                raise SessionNotFound(session_id) from exc

    async def pop(self, session_id: str) -> SessionRuntime | None:
        async with self._lock:
            return self._entries.pop(session_id, None)

    async def aclose(self) -> None:
        async with self._lock:
            runtimes = list(self._entries.values())
            self._entries.clear()
        for runtime in runtimes:
            await runtime.aclose()


__all__ = [
    "SessionAlreadyInvoking",
    "SessionInvokeRunner",
    "SessionNotFound",
    "SessionRegistry",
    "SessionRuntime",
]


DEFAULT_KEEPALIVE_S = 15.0


# Re-export for type-checker friendliness in callers.
async def stream_to_async_iter(
    runtime: SessionRuntime,
    *,
    replay_since: int | None = None,
    keepalive_s: float = DEFAULT_KEEPALIVE_S,
) -> AsyncIterator[bytes]:
    """Yield SSE frames suitable for a `StreamingResponse(... media_type=\"text/event-stream\")`.

    Replays events with `seq > replay_since` first (if requested), then
    subscribes to live events. Honors back-pressure via the per-listener
    queue, sends a keepalive ``:keepalive`` comment every ``keepalive_s``
    seconds so proxies don't close the socket. Tests override the
    timeout to exercise the keepalive path without waiting 15 s.
    """
    saw_terminal_in_replay = False
    if replay_since is not None:
        for past in await runtime.bus.replay(replay_since):
            yield past.to_sse()
            if past.kind in {SessionEventKind.DONE, SessionEventKind.ERROR}:
                saw_terminal_in_replay = True
    if saw_terminal_in_replay:
        # Nothing to live-stream — the invocation is already done. The
        # client can reconnect after the next `invoke` if it wants more.
        return

    queue = await runtime.bus.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_s)
            except TimeoutError:
                yield b": keepalive\n\n"
                continue
            if event is None:
                return
            yield event.to_sse()
            if event.kind in {SessionEventKind.DONE, SessionEventKind.ERROR}:
                return
    finally:
        await runtime.bus.unsubscribe(queue)
