"""Phase L.1 — multi-turn conversation memory via PipelineState.

The pre-L.1 `_drive_pipeline` called `pipeline.run_stream(message)`
without passing a state — the executor created a fresh `PipelineState`
each invoke, so the agent never saw prior turns. These tests verify
the fix: a single `runtime.conversation_state` is reused across
invokes so messages accumulate.

We don't drive a real LLM here; the fake pipeline records what state
was passed in and yields a synthesised `text.delta` so the
SessionRuntime's `text` event fires. The accumulator assertions live
in `test_session_recording.py` — this file is strictly about state
threading.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest

from gapt_server.agent.hooks.cost_hook import CostAccumulator
from gapt_server.agent.session_registry import (
    SessionRuntime,
    _default_invoke_runner,
)


# ──────────────────────────────────────── fake pipeline ──


@dataclass
class _FakeEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    stage: str = ""


@dataclass
class _StateAwarePipeline:
    """Records every `state` it was handed + mutates it the way the
    real executor's input + api stages would: append the user message,
    then a fake assistant reply containing the current message count
    (so the test can assert the state is the same object across
    invokes)."""

    received_states: list[Any] = field(default_factory=list)

    async def run_stream(
        self, message: str, state: Any = None
    ) -> AsyncIterator[_FakeEvent]:
        self.received_states.append(state)
        # Mirror stage 1 (input) — append the user message.
        if state is not None:
            state.messages.append({"role": "user", "content": message})
            # Mirror stage 6 (api) — append a synthetic assistant reply.
            assistant_reply = f"ack #{len([m for m in state.messages if m['role'] == 'user'])}"
            state.messages.append(
                {"role": "assistant", "content": assistant_reply}
            )
            yield _FakeEvent(type="text.delta", data={"text": assistant_reply})

    def attach_runtime(self, *, hook_runner: Any) -> None:
        del hook_runner


def _make_runtime(pipeline: _StateAwarePipeline) -> SessionRuntime:
    return SessionRuntime(
        session_id="s1",
        project_id="p1",
        workspace_id="w1",
        user_id="u1",
        pipeline=pipeline,  # type: ignore[arg-type]
        accumulator=CostAccumulator(session_id="s1"),
    )


# ─────────────────────────────────────────── L.1 ──


@pytest.mark.asyncio
async def test_first_invoke_lazy_creates_state_with_session_id() -> None:
    """A fresh runtime has `conversation_state=None`. The first invoke
    lazy-creates a PipelineState anchored to the session id so the
    executor's session-scoped hooks have a stable key."""
    pipeline = _StateAwarePipeline()
    runtime = _make_runtime(pipeline)
    assert runtime.conversation_state is None

    await _default_invoke_runner(runtime, "hello")
    await asyncio.sleep(0)

    state = runtime.conversation_state
    assert state is not None
    assert state.session_id == "s1"
    # The fake pipeline pushed user + assistant into state.messages.
    assert state.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "ack #1"},
    ]


@pytest.mark.asyncio
async def test_second_invoke_reuses_same_state_object() -> None:
    """The point of L.1: turn 2 sees turn 1's messages. The same state
    instance flows through both calls so the executor pulls prior
    conversation from `state.messages`."""
    pipeline = _StateAwarePipeline()
    runtime = _make_runtime(pipeline)

    await _default_invoke_runner(runtime, "내 이름은 alice")
    await _default_invoke_runner(runtime, "내 이름이 뭐였지?")
    await asyncio.sleep(0)

    # Identity check — the runtime kept the same state reference, so
    # both calls saw the *same* object.
    assert pipeline.received_states[0] is pipeline.received_states[1]
    assert pipeline.received_states[0] is runtime.conversation_state

    state = runtime.conversation_state
    assert state is not None
    # Four entries: user → assistant → user → assistant.
    assert [m["role"] for m in state.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert state.messages[0]["content"] == "내 이름은 alice"
    assert state.messages[2]["content"] == "내 이름이 뭐였지?"


@pytest.mark.asyncio
async def test_runtime_preloaded_state_is_respected() -> None:
    """The rehydrate path creates a runtime with `conversation_state`
    already populated from session_events. The driver must *not*
    overwrite it on first invoke — it must reuse what's there."""
    from geny_executor.core.state import PipelineState

    pipeline = _StateAwarePipeline()
    runtime = _make_runtime(pipeline)
    preloaded = PipelineState(
        session_id="s1",
        messages=[
            {"role": "user", "content": "earlier prompt"},
            {"role": "assistant", "content": "earlier reply"},
        ],
    )
    runtime.conversation_state = preloaded

    await _default_invoke_runner(runtime, "continue please")
    await asyncio.sleep(0)

    # Same object — driver reused, not recreated.
    assert runtime.conversation_state is preloaded
    # The historical messages are still at the front.
    assert preloaded.messages[0]["content"] == "earlier prompt"
    assert preloaded.messages[1]["content"] == "earlier reply"
    # And the new turn's messages are appended.
    assert preloaded.messages[2] == {"role": "user", "content": "continue please"}
