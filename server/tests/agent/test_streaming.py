"""SessionEvent + SessionEventBus + stream_to_async_iter unit tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from gapt_server.agent.hooks.cost_hook import CostAccumulator
from gapt_server.agent.session_registry import (
    SessionAlreadyInvoking,
    SessionNotFound,
    SessionRegistry,
    SessionRuntime,
    stream_to_async_iter,
)
from gapt_server.agent.streaming import (
    SessionEvent,
    SessionEventBus,
    SessionEventKind,
)

# ─────────────────────────────────────────────────── SessionEvent ──


def test_to_sse_frame_shape() -> None:
    ev = SessionEvent(seq=7, kind=SessionEventKind.TEXT, data={"chunk": "hi"})
    frame = ev.to_sse().decode()
    assert frame.startswith("event: text\n")
    assert "\nid: 7\n" in frame
    assert "\ndata: " in frame
    assert frame.endswith("\n\n")
    payload_line = next(line for line in frame.splitlines() if line.startswith("data:"))
    body = json.loads(payload_line[len("data: ") :])
    assert body == {"chunk": "hi"}


def test_to_dict_round_trip() -> None:
    ev = SessionEvent(seq=1, kind=SessionEventKind.COST, data={"tool_calls": 3})
    d = ev.to_dict()
    assert d["seq"] == 1
    assert d["kind"] == "cost"
    assert d["data"] == {"tool_calls": 3}
    assert isinstance(d["ts"], str)


# ──────────────────────────────────────────────── SessionEventBus ──


@pytest.mark.asyncio
async def test_publish_assigns_monotonic_seq() -> None:
    bus = SessionEventBus()
    a = await bus.publish(SessionEventKind.TEXT, {"chunk": "1"})
    b = await bus.publish(SessionEventKind.TEXT, {"chunk": "2"})
    assert (a.seq, b.seq) == (1, 2)


@pytest.mark.asyncio
async def test_subscribe_receives_subsequent_events() -> None:
    bus = SessionEventBus()
    q = await bus.subscribe()
    await bus.publish(SessionEventKind.TEXT, {"chunk": "live"})
    ev = await asyncio.wait_for(q.get(), timeout=0.5)
    assert ev is not None
    assert ev.kind is SessionEventKind.TEXT
    assert ev.data == {"chunk": "live"}


@pytest.mark.asyncio
async def test_replay_returns_only_events_after_since() -> None:
    bus = SessionEventBus()
    for i in range(5):
        await bus.publish(SessionEventKind.TEXT, {"i": i})
    rest = await bus.replay(since=2)
    assert [e.data["i"] for e in rest] == [2, 3, 4]


@pytest.mark.asyncio
async def test_history_limit_drops_oldest() -> None:
    bus = SessionEventBus(history_limit=3)
    for i in range(5):
        await bus.publish(SessionEventKind.TEXT, {"i": i})
    rest = await bus.replay(since=0)
    assert [e.data["i"] for e in rest] == [2, 3, 4]


@pytest.mark.asyncio
async def test_persister_called_with_each_event() -> None:
    """Phase D.3 — the optional persister fires once per publish,
    outside the bus lock, with the freshly-assigned event."""
    captured: list[SessionEvent] = []

    async def sink(event: SessionEvent) -> None:
        captured.append(event)

    bus = SessionEventBus(persister=sink)
    a = await bus.publish(SessionEventKind.TEXT, {"i": 1})
    b = await bus.publish(SessionEventKind.TEXT, {"i": 2})
    assert [e.seq for e in captured] == [a.seq, b.seq]


@pytest.mark.asyncio
async def test_persister_failure_does_not_break_publish() -> None:
    """A flaky DB sink mustn't drop the live stream. publish() must
    still return the event and the subscriber must still receive
    it even when the persister raises."""

    async def angry_sink(_event: SessionEvent) -> None:
        raise RuntimeError("DB is down")

    bus = SessionEventBus(persister=angry_sink)
    sub = await bus.subscribe()
    event = await bus.publish(SessionEventKind.TEXT, {"x": 1})
    delivered = await asyncio.wait_for(sub.get(), timeout=0.5)
    assert delivered is not None and delivered.seq == event.seq


@pytest.mark.asyncio
async def test_seed_seq_starts_next_publish_from_correct_value() -> None:
    """Phase D.3 — rehydrating a session after a restart: tell the
    bus the highest seq already in DB, then the next publish must
    be `last + 1` so there's no collision with persisted rows."""
    bus = SessionEventBus()
    bus.seed_seq(42)
    event = await bus.publish(SessionEventKind.TEXT, {"x": 1})
    assert event.seq == 43


@pytest.mark.asyncio
async def test_seed_seq_does_not_regress() -> None:
    """seed_seq must be monotonic — accidentally calling it with a
    lower value than the current seq must NOT roll the counter
    backwards."""
    bus = SessionEventBus()
    await bus.publish(SessionEventKind.TEXT, {})  # seq=1
    await bus.publish(SessionEventKind.TEXT, {})  # seq=2
    bus.seed_seq(1)  # lower than current — should be a no-op
    next_event = await bus.publish(SessionEventKind.TEXT, {})
    assert next_event.seq == 3


@pytest.mark.asyncio
async def test_close_emits_sentinel_to_subscribers() -> None:
    bus = SessionEventBus()
    q = await bus.subscribe()
    await bus.close()
    sentinel = await asyncio.wait_for(q.get(), timeout=0.5)
    assert sentinel is None


# ──────────────────────────────────────────────── SessionRuntime ──


class _StubPipeline:
    """Minimal Pipeline stand-in — not used by the explicit runner."""


async def _scripted_runner(runtime: SessionRuntime, message: str) -> None:
    await runtime.bus.publish(SessionEventKind.TEXT, {"chunk": f"echo: {message}"})
    await runtime.bus.publish(SessionEventKind.TOOL_CALL, {"tool": "gapt_read"})
    await runtime.bus.publish(SessionEventKind.TOOL_RESULT, {"ok": True})


def _make_runtime(session_id: str = "s1") -> SessionRuntime:
    return SessionRuntime(
        session_id=session_id,
        project_id="p",
        workspace_id="w",
        user_id="u",
        pipeline=_StubPipeline(),  # type: ignore[arg-type]
        accumulator=CostAccumulator(session_id=session_id),
    )


@pytest.mark.asyncio
async def test_invoke_runs_runner_to_completion_and_emits_done() -> None:
    rt = _make_runtime()
    await rt.invoke("hello", runner=_scripted_runner)
    await rt.wait_done()
    events = await rt.bus.replay(since=0)
    kinds = [e.kind.value for e in events]
    assert kinds == ["text", "tool_call", "tool_result", "done"]


@pytest.mark.asyncio
async def test_invoke_twice_while_running_raises() -> None:
    rt = _make_runtime()

    async def slow_runner(runtime: SessionRuntime, message: str) -> None:
        await asyncio.sleep(0.05)

    await rt.invoke("first", runner=slow_runner)
    with pytest.raises(SessionAlreadyInvoking):
        await rt.invoke("second", runner=slow_runner)
    await rt.wait_done()


@pytest.mark.asyncio
async def test_interrupt_cancels_running_invoke_and_emits_error() -> None:
    rt = _make_runtime()

    async def forever(runtime: SessionRuntime, message: str) -> None:
        await asyncio.sleep(10)

    await rt.invoke("never", runner=forever)
    # Yield so the background task actually enters the `await sleep`
    # before we cancel — otherwise the cancellation may land before any
    # frame in `_run_with_lifecycle` executes.
    await asyncio.sleep(0)
    cancelled = await rt.interrupt()
    assert cancelled is True
    await rt.wait_done()

    events = await rt.bus.replay(since=0)
    assert any(
        e.kind is SessionEventKind.ERROR and e.data.get("exec_code") == "exec.session.cancelled"
        for e in events
    )


@pytest.mark.asyncio
async def test_interrupt_when_idle_returns_false() -> None:
    rt = _make_runtime()
    assert await rt.interrupt() is False


@pytest.mark.asyncio
async def test_invoke_crash_emits_error_with_exec_code() -> None:
    rt = _make_runtime()

    async def crashing(runtime: SessionRuntime, message: str) -> None:
        raise RuntimeError("kaboom")

    await rt.invoke("crash", runner=crashing)
    await rt.wait_done()

    events = await rt.bus.replay(since=0)
    err = next(e for e in events if e.kind is SessionEventKind.ERROR)
    assert err.data["exec_code"] == "exec.session.crashed"
    assert "kaboom" in err.data["reason"]


# ──────────────────────────────────────────────── SessionRegistry ──


@pytest.mark.asyncio
async def test_registry_round_trip() -> None:
    reg = SessionRegistry()
    rt = _make_runtime("abc")
    await reg.register(rt)

    fetched = await reg.get("abc")
    assert fetched is rt

    popped = await reg.pop("abc")
    assert popped is rt

    with pytest.raises(SessionNotFound):
        await reg.get("abc")


# ──────────────────────────────────────────────── stream_to_async_iter ──


@pytest.mark.asyncio
async def test_stream_replays_then_streams_live() -> None:
    rt = _make_runtime()
    # Pre-populate one event so replay path fires.
    await rt.bus.publish(SessionEventKind.TEXT, {"chunk": "past"})

    frames: list[bytes] = []

    async def reader() -> None:
        async for frame in stream_to_async_iter(rt, replay_since=0):
            frames.append(frame)

    reader_task = asyncio.create_task(reader())
    # Give the reader a tick to subscribe + flush replay.
    await asyncio.sleep(0)
    await rt.bus.publish(SessionEventKind.TEXT, {"chunk": "live"})
    await rt.bus.publish(SessionEventKind.DONE, {"cost": {}})

    await asyncio.wait_for(reader_task, timeout=1.0)

    decoded = [f.decode() for f in frames]
    # Past + live + done + retry hint.
    assert any("past" in f for f in decoded)
    assert any("live" in f for f in decoded)
    assert any(f.startswith("event: done\n") for f in decoded)
    # Terminal events emit a 1-day retry hint so the browser's
    # EventSource does not auto-reconnect (which would surface as a
    # spurious "Stream interrupted" banner in the chat panel).
    assert decoded[-1] == "retry: 86400000\n\n"


@pytest.mark.asyncio
async def test_stream_keepalive_on_idle() -> None:
    """Use the configurable keepalive timeout to exercise the idle
    branch without waiting 15 s."""

    rt = _make_runtime()
    frames: list[bytes] = []

    async def reader() -> None:
        async for frame in stream_to_async_iter(rt, keepalive_s=0.01):
            frames.append(frame)
            if frame.startswith(b": keepalive"):
                await rt.bus.close()

    await asyncio.wait_for(reader(), timeout=1.0)
    assert any(f.startswith(b": keepalive") for f in frames)
