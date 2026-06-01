"""Phase I.1 + I.2 + I.3 — wire-up tests for session recording.

Drives a fake `Pipeline.run_stream` that emits the canonical executor
event vocabulary (`text.delta` / `token.tracked` / etc.) and asserts:

- token.tracked invokes `runtime.cost_callback` (I.1) so the DB write
  fires for tool-less chat sessions.
- `_run_with_lifecycle` publishes a `user_message` event as the FIRST
  bus frame of the turn (I.2).
- When the executor's `cost_usd` is 0 but tokens are positive, the
  GAPT-side fallback (I.3) fills in a positive cost using the
  runtime's `model_name` alias.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest

from gapt_server.agent.hooks.cost_hook import CostAccumulator
from gapt_server.agent.session_registry import (
    SessionRuntime,
    _run_with_lifecycle,
    _default_invoke_runner,
)


# ──────────────────────────────────────── fake pipeline ──


@dataclass
class _FakeEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    stage: str = ""


@dataclass
class _FakePipeline:
    """Mirrors the subset of `geny_executor.Pipeline` the registry
    touches: an async `run_stream(message)` generator. Tests provide
    the event list at construction; the pipeline replays them in
    order so we can shape the scenario."""

    events: list[_FakeEvent]

    async def run_stream(self, message: str, state=None) -> AsyncIterator[_FakeEvent]:
        del message
        for ev in self.events:
            yield ev

    def attach_runtime(self, *, hook_runner: Any) -> None:
        del hook_runner


# ───────────────────────────────────────── fixtures ──


def _make_runtime(
    pipeline: _FakePipeline,
    *,
    model_name: str | None = None,
    cost_callback: Any = None,
) -> SessionRuntime:
    return SessionRuntime(
        session_id="s1",
        project_id="p1",
        workspace_id="w1",
        user_id="u1",
        pipeline=pipeline,  # type: ignore[arg-type]
        accumulator=CostAccumulator(session_id="s1"),
        model_name=model_name,
        cost_callback=cost_callback,
    )


# ─────────────────────────────────────────── I.1 ──


@pytest.mark.asyncio
async def test_cost_callback_fires_on_token_tracked() -> None:
    """token.tracked must route through `runtime.cost_callback`. The
    pre-Phase-I code only published a COST event and never wrote to
    the DB — this test would have caught that as soon as it landed."""
    pipeline = _FakePipeline(
        events=[
            _FakeEvent(
                type="token.tracked",
                data={
                    "input_tokens": 100,
                    "output_tokens": 200,
                    "cost_usd": 0.003,
                },
            ),
        ]
    )
    seen: list[CostAccumulator] = []

    async def _cb(acc: CostAccumulator) -> None:
        seen.append(acc)

    runtime = _make_runtime(pipeline, cost_callback=_cb)
    await _default_invoke_runner(runtime, "hello")
    # Drain any pending publishes the lifecycle wrapper queued.
    await asyncio.sleep(0)
    assert len(seen) == 1
    assert seen[0].input_tokens == 100
    assert seen[0].output_tokens == 200
    assert seen[0].cost_usd == pytest.approx(0.003)


@pytest.mark.asyncio
async def test_legacy_path_still_publishes_cost_event() -> None:
    """When no cost_callback is wired (older test paths), the COST
    SSE frame must still be published so the chat header doesn't
    silently freeze."""
    pipeline = _FakePipeline(
        events=[
            _FakeEvent(
                type="token.tracked",
                data={"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001},
            ),
        ]
    )
    runtime = _make_runtime(pipeline)
    seen: list[tuple[str, dict[str, Any]]] = []

    async def _capture(kind: Any, data: dict[str, Any]) -> None:
        seen.append((str(kind), data))

    runtime.bus.publish = _capture  # type: ignore[assignment]
    # Bypass the lifecycle wrapper — this test is specifically about
    # the legacy fallback inside `_drive_pipeline` (no callback wired).
    await _default_invoke_runner(runtime, "hi")
    kinds = [k for k, _ in seen]
    assert any("cost" in k.lower() for k in kinds), kinds


# ─────────────────────────────────────────── I.2 ──


@pytest.mark.asyncio
async def test_user_message_published_as_first_event() -> None:
    """`_run_with_lifecycle` must publish USER_MESSAGE before the
    runner runs. The chat replay then has the user side of every turn.
    Test uses a no-op runner so the only events on the bus are the
    user_message + done frames."""
    pipeline = _FakePipeline(events=[])
    runtime = _make_runtime(pipeline)
    seen: list[tuple[Any, dict[str, Any]]] = []

    async def _capture(kind: Any, data: dict[str, Any]) -> None:
        seen.append((kind, data))

    runtime.bus.publish = _capture  # type: ignore[assignment]

    async def _noop(_rt: SessionRuntime, _msg: str) -> None:
        return None

    await _run_with_lifecycle(runtime, "what is 1+1?", _noop)
    assert seen, "lifecycle should have published at least one event"
    first_kind, first_data = seen[0]
    # The kind comes back as the StrEnum value or its repr depending
    # on call site — both forms surface "user_message" once stringified.
    assert "user_message" in str(first_kind).lower()
    assert first_data == {"text": "what is 1+1?"}


# ─────────────────────────────────────────── I.3 ──


@pytest.mark.asyncio
async def test_alias_fallback_fills_cost_when_executor_reports_zero() -> None:
    """The model-alias bug: manifest says `"model":"sonnet"`, upstream
    pricing dict only has canonical ids, so the token stage emits
    `cost_usd:0`. GAPT's fallback must catch that and produce a
    positive cost using the resolved canonical id."""
    pipeline = _FakePipeline(
        events=[
            _FakeEvent(
                type="token.tracked",
                data={
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "cost_usd": 0.0,  # ← the bug being patched around
                },
            ),
        ]
    )
    captured: list[float] = []

    async def _cb(acc: CostAccumulator) -> None:
        captured.append(acc.cost_usd)

    runtime = _make_runtime(pipeline, model_name="sonnet", cost_callback=_cb)
    await _default_invoke_runner(runtime, "hi")
    assert captured, "cost_callback must fire on token.tracked"
    # Sonnet 4.6 is $3/M input + $15/M output = $18 for 1M+1M.
    assert captured[0] == pytest.approx(18.0, rel=1e-3)


@pytest.mark.asyncio
async def test_cache_tokens_tracked_in_accumulator() -> None:
    """Phase K.2 — the token.tracked payload's `cache_write` /
    `cache_read` keys must end up on the accumulator + propagate to
    the cost_callback so the DB row + cost dashboard can show them.
    Pre-K.2 these were used inside `compute_cost_usd` but discarded
    after, leaving the operator with "6 tokens for $0.013" and no
    way to see where the money went."""
    pipeline = _FakePipeline(
        events=[
            _FakeEvent(
                type="token.tracked",
                data={
                    "input_tokens": 6,
                    "output_tokens": 6,
                    "cache_write": 3400,
                    "cache_read": 200,
                    "cost_usd": 0.0,
                },
            ),
        ]
    )
    seen: list[CostAccumulator] = []

    async def _cb(acc: CostAccumulator) -> None:
        seen.append(acc)

    runtime = _make_runtime(pipeline, model_name="sonnet", cost_callback=_cb)
    await _default_invoke_runner(runtime, "hi")
    assert seen, "cost_callback must fire when cache tokens land"
    acc = seen[-1]
    assert acc.cache_write_tokens == 3400
    assert acc.cache_read_tokens == 200
    # Snapshot must surface them too — the SSE COST frame relies on it.
    snap = acc.snapshot()
    assert snap["cache_write_tokens"] == 3400
    assert snap["cache_read_tokens"] == 200


@pytest.mark.asyncio
async def test_alias_fallback_skipped_when_executor_supplies_cost() -> None:
    """When the executor's `cost_usd` is non-zero, the fallback path
    must NOT run — otherwise a session running against a properly-
    configured manifest would double-bill."""
    pipeline = _FakePipeline(
        events=[
            _FakeEvent(
                type="token.tracked",
                data={
                    "input_tokens": 1000,
                    "output_tokens": 1000,
                    "cost_usd": 0.42,
                },
            ),
        ]
    )
    captured: list[float] = []

    async def _cb(acc: CostAccumulator) -> None:
        captured.append(acc.cost_usd)

    runtime = _make_runtime(pipeline, model_name="sonnet", cost_callback=_cb)
    await _default_invoke_runner(runtime, "hi")
    # No fallback addition — the accumulator carries exactly the
    # executor-supplied 0.42.
    assert captured[-1] == pytest.approx(0.42)
