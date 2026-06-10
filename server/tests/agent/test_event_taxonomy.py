"""geny-executor 2.2.0 event taxonomy — `_drive_pipeline` mapping tests.

2.2.0's Stage 6 forwards the full canonical chunk set as run_stream
events (the capability GAPT previously monkey-patched
`_call_streaming` to get). These tests drive the real `_drive_pipeline`
with a scripted pipeline emitting the new names and assert the
SessionEvent frames the chat UI depends on:

- `api.tool_use`  → TOOL_CALL frame (the gap the old patch docstring
  complained about: "the GAPT chat trace has no idea any tool ran")
- `api.cli_tool_call` → suppressed (narrow duplicate of api.tool_use)
- `api.tool_result` → TOOL_RESULT frame with flattened content
- `thinking.delta` → STEP frame (phase "thinking") in the trace
- `api.error` → STEP frame (phase "api_error") carrying the exec code
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from gapt_server.agent.hooks.cost_hook import CostAccumulator
from gapt_server.agent.session_registry import SessionRuntime, _drive_pipeline
from gapt_server.agent.streaming import SessionEventKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class _FakeEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    stage: str = ""


@dataclass
class _ScriptedPipeline:
    events: list[_FakeEvent]

    async def run_stream(
        self, message: str, state: Any = None, **kwargs: Any
    ) -> AsyncIterator[_FakeEvent]:
        del message, state, kwargs
        for ev in self.events:
            yield ev


def _runtime(pipeline: _ScriptedPipeline) -> SessionRuntime:
    return SessionRuntime(
        session_id="ev",
        project_id="p",
        workspace_id="w",
        user_id="u",
        pipeline=pipeline,  # type: ignore[arg-type]
        accumulator=CostAccumulator(session_id="ev"),
    )


async def _drive(events: list[_FakeEvent]) -> list[tuple[SessionEventKind, dict]]:
    rt = _runtime(_ScriptedPipeline(events=events))
    published: list[tuple[SessionEventKind, dict]] = []

    async def _capture(kind: SessionEventKind, data: dict) -> None:
        published.append((kind, data))

    rt.bus.publish = _capture  # type: ignore[assignment]
    await _drive_pipeline(rt, "go")
    return published


@pytest.mark.asyncio
async def test_api_tool_use_maps_to_tool_call_frame() -> None:
    published = await _drive(
        [
            _FakeEvent(
                type="api.tool_use",
                data={
                    "id": "toolu_01",
                    "name": "Bash",
                    "input": {"command": "ls -la"},
                    "source": "cli",
                },
            ),
        ]
    )
    calls = [d for k, d in published if k is SessionEventKind.TOOL_CALL]
    assert len(calls) == 1
    assert calls[0]["tool"] == "Bash"
    assert calls[0]["tool_use_id"] == "toolu_01"
    assert calls[0]["input"] == {"command": "ls -la"}
    # Trace row fires too (phase tool_invoke with the command hint).
    steps = [d for k, d in published if k is SessionEventKind.STEP]
    assert any(d["phase"] == "tool_invoke" and "ls -la" in d["summary"] for d in steps)


@pytest.mark.asyncio
async def test_api_cli_tool_call_companion_is_suppressed() -> None:
    """`api.cli_tool_call` duplicates `api.tool_use` for narrow
    subscribers — forwarding both would double-render every
    ToolCallCard."""
    published = await _drive(
        [
            _FakeEvent(
                type="api.tool_use",
                data={"id": "t1", "name": "Read", "input": {}, "source": "cli"},
            ),
            _FakeEvent(
                type="api.cli_tool_call",
                data={"id": "t1", "name": "Read", "input": {}, "source": "cli"},
            ),
        ]
    )
    calls = [d for k, d in published if k is SessionEventKind.TOOL_CALL]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_api_tool_result_maps_to_tool_result_frame_with_flattened_content() -> None:
    published = await _drive(
        [
            _FakeEvent(
                type="api.tool_result",
                data={
                    "tool_use_id": "toolu_01",
                    "content": [
                        {"type": "text", "text": "file1\n"},
                        {"type": "text", "text": "file2\n"},
                    ],
                    "is_error": False,
                    "source": "cli",
                },
            ),
        ]
    )
    results = [d for k, d in published if k is SessionEventKind.TOOL_RESULT]
    assert len(results) == 1
    assert results[0]["tool_use_id"] == "toolu_01"
    assert results[0]["content"] == "file1\nfile2\n"
    assert results[0]["output"] == "file1\nfile2\n"
    assert results[0]["is_error"] is False


@pytest.mark.asyncio
async def test_thinking_delta_lands_in_step_trace() -> None:
    published = await _drive([_FakeEvent(type="thinking.delta", data={"text": "hmm"})])
    steps = [d for k, d in published if k is SessionEventKind.STEP]
    assert any(d["phase"] == "thinking" for d in steps)
    # Thinking is trace-only — it must NOT leak into the chat text.
    assert not any(k is SessionEventKind.TEXT for k, _ in published)


@pytest.mark.asyncio
async def test_api_error_lands_in_step_trace_with_code() -> None:
    published = await _drive(
        [
            _FakeEvent(
                type="api.error",
                data={
                    "code": "exec.api.rate_limited",
                    "category": "rate_limit",
                    "provider": "claude_code_cli",
                    "message": "429 from upstream",
                },
            ),
        ]
    )
    steps = [d for k, d in published if k is SessionEventKind.STEP]
    assert any(d["phase"] == "api_error" and "exec.api.rate_limited" in d["summary"] for d in steps)


@pytest.mark.asyncio
async def test_streaming_bookkeeping_frames_are_suppressed() -> None:
    """Raw `api.input_json_delta` / `api.content_block_stop` fragments
    are protocol bookkeeping, not UI material."""
    published = await _drive(
        [
            _FakeEvent(type="api.input_json_delta", data={"delta": '{"co'}),
            _FakeEvent(type="api.content_block_stop", data={}),
        ]
    )
    assert published == []


@pytest.mark.asyncio
async def test_text_delta_still_maps_to_text_frame() -> None:
    published = await _drive([_FakeEvent(type="text.delta", data={"text": "hi"})])
    texts = [d for k, d in published if k is SessionEventKind.TEXT]
    assert texts == [{"text": "hi"}]
