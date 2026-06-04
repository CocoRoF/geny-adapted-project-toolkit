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
    # Phase I.2 — `_run_with_lifecycle` prefixes every invoke with a
    # user_message event so the transcript carries both sides.
    assert kinds == ["user_message", "text", "tool_call", "tool_result", "done"]


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
    """Phase L follow-up — the SSE stream stays open for the session's
    full lifetime (multi-turn). DONE no longer terminates; only an
    explicit bus close does. The reader exits when we call
    `bus.close()` after the test's last assertion."""
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
    # Simulate a follow-up turn — pre-Phase-L the stream had already
    # closed by this point and these frames went into the void. With
    # the keep-alive contract they land on the same reader.
    await rt.bus.publish(SessionEventKind.TEXT, {"chunk": "turn2"})
    # Tear the reader down by closing the bus.
    await rt.bus.close()

    await asyncio.wait_for(reader_task, timeout=1.0)

    decoded = [f.decode() for f in frames]
    assert any("past" in f for f in decoded)
    assert any("live" in f for f in decoded)
    assert any(f.startswith("event: done\n") for f in decoded)
    # Phase L proof — turn-2 text arrived on the same stream.
    assert any("turn2" in f for f in decoded)
    # No "retry:" hint any more — the stream stays open, so the
    # browser never needs the back-off instruction.
    assert not any(f.startswith("retry:") for f in decoded)


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


# ────────────────────────────────────── SessionRegistry LRU + idle ──
# Phase M.1 — operator-tunable memory bounds. The registry now evicts
# in two scenarios: a `register()` that would push the size past
# `max_size`, and the background `_idle_sweep` loop. These tests pin
# both behaviours so a future refactor can't silently raise the
# worst-case memory ceiling.


@pytest.mark.asyncio
async def test_registry_lru_evicts_oldest_on_overflow() -> None:
    reg = SessionRegistry()
    reg.configure(max_size=2, idle_eviction_s=3600.0)

    rt_a = _make_runtime("a")
    rt_b = _make_runtime("b")
    rt_c = _make_runtime("c")
    await reg.register(rt_a)
    await reg.register(rt_b)
    # Touch `a` so `b` becomes the LRU; registering `c` should drop `b`.
    await reg.get("a")
    await reg.register(rt_c)

    assert await reg.get("a") is rt_a
    assert await reg.get("c") is rt_c
    with pytest.raises(SessionNotFound):
        await reg.get("b")


@pytest.mark.asyncio
async def test_registry_idle_sweep_evicts_past_window() -> None:
    reg = SessionRegistry()
    # Fast sweep + tiny idle window so the test completes in <0.2 s.
    reg.configure(max_size=10, idle_eviction_s=0.05)
    reg.sweep_interval_s = 0.02

    rt = _make_runtime("stale")
    await reg.register(rt)
    reg.start_sweep()
    try:
        # Wait long enough that `now - last_active_at > idle_eviction_s`
        # and the sweep had at least one tick to evict.
        await asyncio.sleep(0.2)
        with pytest.raises(SessionNotFound):
            await reg.get("stale")
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_registry_touch_keeps_active_session_warm() -> None:
    """Active session bumps `last_active_at` on every `get` — the idle
    sweep must not evict it even though wall-clock would say it's old."""
    reg = SessionRegistry()
    reg.configure(max_size=10, idle_eviction_s=0.1)
    reg.sweep_interval_s = 0.02

    rt = _make_runtime("warm")
    await reg.register(rt)
    reg.start_sweep()
    try:
        # Keep touching while the sweep would otherwise evict.
        for _ in range(5):
            await asyncio.sleep(0.03)
            await reg.get("warm")
        assert await reg.get("warm") is rt
    finally:
        await reg.aclose()


# ────────────────────────────────────── invoke driver state cap ──


@pytest.mark.asyncio
async def test_drive_pipeline_trims_state_messages_to_cap() -> None:
    """`_drive_pipeline` must trim `state.messages` from the head so the
    next `Pipeline.run_stream` doesn't push the model's context window.
    Mocks the pipeline so the trim is exercised without geny-executor."""
    from geny_executor.core.state import PipelineState

    from gapt_server.agent.session_registry import _drive_pipeline

    class _CapturePipeline:
        def __init__(self) -> None:
            self.seen_message_count: int | None = None

        async def run_stream(self, message: str, *, state):  # type: ignore[no-untyped-def]
            self.seen_message_count = len(state.messages)
            if False:  # pragma: no cover — keep the async generator shape
                yield

    pipe = _CapturePipeline()
    rt = SessionRuntime(
        session_id="cap",
        project_id="p",
        workspace_id="w",
        user_id="u",
        pipeline=pipe,  # type: ignore[arg-type]
        accumulator=CostAccumulator(session_id="cap"),
        max_state_messages=4,
    )
    # Seed a state with more entries than the cap — `_drive_pipeline`
    # should drop the head before calling `run_stream`.
    rt.conversation_state = PipelineState(
        session_id="cap",
        messages=[
            {"role": "user", "content": "old-1"},
            {"role": "assistant", "content": "old-2"},
            {"role": "user", "content": "old-3"},
            {"role": "assistant", "content": "old-4"},
            {"role": "user", "content": "recent-5"},
            {"role": "assistant", "content": "recent-6"},
        ],
    )

    await _drive_pipeline(rt, "next prompt")

    assert pipe.seen_message_count == 4
    # The two oldest entries are gone; the most-recent four remain.
    kept = [m["content"] for m in rt.conversation_state.messages]
    assert kept == ["old-3", "old-4", "recent-5", "recent-6"]


# ──────────────────────────────────── per-invoke overrides + revert ──
# Phase M.2 — the prior implementation mutated `state.model` which the
# executor's `_init_state` resets on every `run_stream`, so "switch to
# opus for one follow-up" never took effect. The new path mutates
# `pipeline._config.model.*` and snapshots a baseline so `clear=[...]`
# restores it.


class _FakeModelCfg:
    """Stand-in for `geny_executor.core.config.ModelConfig` — only the
    attributes the override helper touches."""

    def __init__(self, *, model: str, thinking_enabled: bool, thinking_budget_tokens: int) -> None:
        self.model = model
        self.thinking_enabled = thinking_enabled
        self.thinking_budget_tokens = thinking_budget_tokens


class _FakePipelineConfig:
    def __init__(self, model_cfg: _FakeModelCfg) -> None:
        self.model = model_cfg


class _FakePipeline:
    """Carries `_config` with the same shape `Pipeline` exposes so the
    runtime helper can poke at it without instantiating the executor."""

    def __init__(self, *, model: str = "claude-sonnet-4-6", thinking_enabled: bool = False, thinking_budget_tokens: int = 10000) -> None:
        self._config = _FakePipelineConfig(
            _FakeModelCfg(
                model=model,
                thinking_enabled=thinking_enabled,
                thinking_budget_tokens=thinking_budget_tokens,
            )
        )


def _override_runtime(pipeline: _FakePipeline) -> SessionRuntime:
    return SessionRuntime(
        session_id="ov",
        project_id="p",
        workspace_id="w",
        user_id="u",
        pipeline=pipeline,  # type: ignore[arg-type]
        accumulator=CostAccumulator(session_id="ov"),
    )


def test_apply_per_invoke_overrides_mutates_pipeline_config_not_state() -> None:
    """`state.model` would be wiped by `_init_state` — the override has
    to land on `pipeline._config.model.*` to survive. Asserts the new
    write path."""
    pipeline = _FakePipeline(model="claude-sonnet-4-6")
    rt = _override_runtime(pipeline)

    rt.apply_per_invoke_overrides(
        model="claude-opus-4-7",
        thinking_enabled=None,
        thinking_budget_tokens=None,
        clear=None,
    )

    assert pipeline._config.model.model == "claude-opus-4-7"
    assert rt.model_name == "claude-opus-4-7"
    # Baseline was snapshotted so a later `clear` can restore it.
    assert rt._baseline_model == "claude-sonnet-4-6"


def test_apply_per_invoke_overrides_clear_restores_baseline() -> None:
    pipeline = _FakePipeline(
        model="claude-sonnet-4-6", thinking_enabled=False, thinking_budget_tokens=10000
    )
    rt = _override_runtime(pipeline)

    # First invoke: override everything.
    rt.apply_per_invoke_overrides(
        model="claude-opus-4-7",
        thinking_enabled=True,
        thinking_budget_tokens=20000,
        clear=None,
    )
    assert pipeline._config.model.model == "claude-opus-4-7"
    assert pipeline._config.model.thinking_enabled is True
    assert pipeline._config.model.thinking_budget_tokens == 20000

    # Second invoke: clear the model + the thinking pair via the alias.
    rt.apply_per_invoke_overrides(
        model=None,
        thinking_enabled=None,
        thinking_budget_tokens=None,
        clear=["model", "thinking"],
    )
    assert pipeline._config.model.model == "claude-sonnet-4-6"
    assert pipeline._config.model.thinking_enabled is False
    assert pipeline._config.model.thinking_budget_tokens == 10000
    # And `model_name` followed the model field so the pricing fallback
    # resolves against the manifest value again.
    assert rt.model_name == "claude-sonnet-4-6"


def test_apply_per_invoke_overrides_clear_wins_over_set_in_same_request() -> None:
    """When both `model` and `clear=["model"]` are passed in one
    request, clear wins. The UI's reset button shouldn't have to also
    blank the input field."""
    pipeline = _FakePipeline(model="claude-sonnet-4-6")
    rt = _override_runtime(pipeline)
    # Set first so baseline is captured.
    rt.apply_per_invoke_overrides(
        model="claude-opus-4-7", thinking_enabled=None, thinking_budget_tokens=None, clear=None
    )
    # Now: try to set haiku AND clear model — clear should win.
    rt.apply_per_invoke_overrides(
        model="claude-haiku-4-5",
        thinking_enabled=None,
        thinking_budget_tokens=None,
        clear=["model"],
    )
    assert pipeline._config.model.model == "claude-sonnet-4-6"


def test_apply_per_invoke_overrides_budget_implies_thinking_enabled() -> None:
    """Budget > 0 without an explicit `thinking_enabled` flips thinking
    on — mirrors the manifest-time `apply_overrides` heuristic."""
    pipeline = _FakePipeline(thinking_enabled=False, thinking_budget_tokens=10000)
    rt = _override_runtime(pipeline)
    rt.apply_per_invoke_overrides(
        model=None,
        thinking_enabled=None,
        thinking_budget_tokens=15000,
        clear=None,
    )
    assert pipeline._config.model.thinking_enabled is True
    assert pipeline._config.model.thinking_budget_tokens == 15000


def test_apply_per_invoke_overrides_noop_without_baseline_capture() -> None:
    """No override + no clear → baseline stays uncaptured so we don't
    waste a snapshot on every invoke that doesn't change anything."""
    pipeline = _FakePipeline()
    rt = _override_runtime(pipeline)
    rt.apply_per_invoke_overrides(
        model=None, thinking_enabled=None, thinking_budget_tokens=None, clear=None
    )
    assert rt._baseline_captured is False
