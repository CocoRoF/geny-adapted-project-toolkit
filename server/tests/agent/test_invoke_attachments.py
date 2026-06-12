"""Chat image attachments — invoke → pipeline input threading.

The composer sends base64 images alongside the message text; the
runtime must hand `run_stream` a dict input (`{"text", "attachments"}`)
so geny-executor's s01 MultimodalNormalizer builds Anthropic image
content blocks. Text-only turns must keep passing the plain string —
that shape is a public seam every legacy pipeline stub relies on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest

from gapt_server.agent.hooks.cost_hook import CostAccumulator
from gapt_server.agent.session_registry import (
    SessionRuntime,
    _default_invoke_runner,
)


@dataclass
class _FakeEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    stage: str = ""


@dataclass
class _InputRecordingPipeline:
    """Records the raw `input` run_stream received."""

    received_inputs: list[Any] = field(default_factory=list)

    async def run_stream(
        self, message: Any, state: Any = None
    ) -> AsyncIterator[_FakeEvent]:
        del state
        self.received_inputs.append(message)
        yield _FakeEvent(type="text.delta", data={"text": "ok"})

    def attach_runtime(self, *, hook_runner: Any) -> None:
        del hook_runner


def _make_runtime(pipeline: _InputRecordingPipeline) -> SessionRuntime:
    return SessionRuntime(
        session_id="s1",
        project_id="p1",
        workspace_id="w1",
        user_id="u1",
        pipeline=pipeline,  # type: ignore[arg-type]
        accumulator=CostAccumulator(session_id="s1"),
    )


_IMAGE = {"kind": "image", "mime_type": "image/png", "data": "aGVsbG8="}


@pytest.mark.asyncio
async def test_text_only_turn_passes_plain_string() -> None:
    pipeline = _InputRecordingPipeline()
    runtime = _make_runtime(pipeline)

    await _default_invoke_runner(runtime, "hello")

    assert pipeline.received_inputs == ["hello"]


@pytest.mark.asyncio
async def test_attachment_turn_passes_multimodal_dict() -> None:
    pipeline = _InputRecordingPipeline()
    runtime = _make_runtime(pipeline)
    runtime._pending_attachments = [_IMAGE]  # what invoke() stashes

    await _default_invoke_runner(runtime, "what is in this image?")

    assert pipeline.received_inputs == [
        {"text": "what is in this image?", "attachments": [_IMAGE]}
    ]


@pytest.mark.asyncio
async def test_attachments_are_consumed_exactly_once() -> None:
    """A crash-retry of the runtime must not resend stale images —
    the stash is popped by the first drive."""
    pipeline = _InputRecordingPipeline()
    runtime = _make_runtime(pipeline)
    runtime._pending_attachments = [_IMAGE]

    await _default_invoke_runner(runtime, "first")
    await _default_invoke_runner(runtime, "second")

    assert pipeline.received_inputs[0] == {
        "text": "first",
        "attachments": [_IMAGE],
    }
    assert pipeline.received_inputs[1] == "second"


@pytest.mark.asyncio
async def test_invoke_stashes_attachments_and_publishes_meta() -> None:
    """End-to-end through `invoke()`: the attachment rides the stash,
    and the persisted user_message event carries META only (media
    type, no base64 blob — events land in the transcript DB)."""
    pipeline = _InputRecordingPipeline()
    runtime = _make_runtime(pipeline)

    await runtime.invoke("look", attachments=[_IMAGE])
    await runtime.wait_done()

    assert pipeline.received_inputs == [
        {"text": "look", "attachments": [_IMAGE]}
    ]
    events = await runtime.bus.replay(0)
    user_events = [ev for ev in events if ev.kind.value == "user_message"]
    assert len(user_events) == 1
    assert user_events[0].data["attachments"] == [
        {"kind": "image", "media_type": "image/png"}
    ]
    assert "data" not in str(user_events[0].data.get("attachments"))
