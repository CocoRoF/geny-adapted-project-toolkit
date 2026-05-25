"""Runtime patches on top of `geny-executor`.

The executor's s06_api default-artifact `_call_streaming` forwards
*only* `text_delta` chunks to `state.add_event`. Other canonical
chunks the provider emits — `tool_use` / `thinking_delta` /
`input_json_delta` / `content_block_stop` — get silently dropped
between the provider stream and the pipeline event bus. The result:
even when the spawned `claude_code_cli` *does* run Bash / Read / Edit
internally, the GAPT chat trace has no idea any tool ran. The user's
"agentic visibility" complaint is the symptom.

Per [[feedback_extend_executor_not_adapter_layer]] the real fix
belongs upstream. This module is a temporary shim that swaps the
method body with one that also forwards `tool_use` (with the input
accumulated across delta frames). Remove when the upstream patch
ships.

Applied once at import time of `gapt_server.agent` (see __init__).
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from geny_executor.llm_client.base import BaseClient
from geny_executor.stages.s06_api.artifact.default.stage import (
    APIError,
    APIStage,
    ErrorCategory,
    ExecutorErrorCode,
    PipelineState,
)


async def _patched_call_streaming(
    self: APIStage,
    client: BaseClient,
    cfg: Any,
    state: PipelineState,
) -> Any:
    """Re-implementation that also forwards `tool_use` chunks.

    Behavioural delta vs upstream:
      * adds `state.add_event("tool.invoke", {tool_use_id, name, input})`
        per accumulated tool_use frame (the provider streams the input
        as a sequence of `input_json_delta` partial-json fragments; we
        join them and emit one event per *complete* tool_use block).
      * adds `state.add_event("thinking.delta", {text: ...})` for the
        thinking content block (lets the UI show "thinking…" beats).

    Everything else is identical — `text_delta` still flows through,
    `message_complete` still wins.
    """
    response: Optional[Any] = None
    kwargs = self._call_kwargs(cfg, state)  # noqa: SLF001 — by-design override

    stream: AsyncIterator[Dict[str, Any]] = client.create_message_stream(**kwargs)

    # Per-index accumulators for tool_use blocks. Provider may emit
    # `tool_use` with the metadata then a sequence of `input_json_delta`
    # frames whose `partial_json` strings concatenate to the full JSON
    # input. We finalise on the matching `content_block_stop`.
    pending_tool: dict[int, dict[str, Any]] = {}
    partial_input: dict[int, list[str]] = {}

    async for chunk in stream:
        chunk_type = chunk.get("type")
        if chunk_type == "message_complete":
            response = chunk["response"]
            continue
        if chunk_type == "text_delta":
            text = chunk.get("text")
            if text:
                state.add_event("text.delta", {"text": text})
            continue
        if chunk_type == "thinking_delta":
            text = chunk.get("text")
            if text:
                state.add_event("thinking.delta", {"text": text})
            continue
        if chunk_type == "tool_use":
            # Two shapes arrive here:
            #   - Form 1 (true streaming): `input` is empty dict, the
            #     real args come later as `input_json_delta` frames
            #     concatenated until `content_block_stop`. We queue
            #     and flush on stop.
            #   - Form 2 (Claude Code 2.x message form): `input` is the
            #     full dict already. Emit immediately because the
            #     accumulator skips emitting `content_block_stop` for
            #     this form.
            idx = int(chunk.get("index", -1))
            input_payload = chunk.get("input") or {}
            if input_payload:
                state.add_event(
                    "tool.invoke",
                    {
                        "tool_use_id": chunk.get("id"),
                        "name": chunk.get("name"),
                        "input": input_payload,
                    },
                )
            else:
                pending_tool[idx] = {
                    "tool_use_id": chunk.get("id"),
                    "name": chunk.get("name"),
                    "input": input_payload,
                }
                partial_input.setdefault(idx, [])
            continue
        if chunk_type == "input_json_delta":
            idx = int(chunk.get("index", -1))
            # The accumulator's translator output uses `delta` (not
            # `partial_json`) as the JSON-fragment field. The provider
            # may pass either depending on shape — accept both.
            frag = chunk.get("delta") or chunk.get("partial_json", "")
            partial_input.setdefault(idx, []).append(str(frag))
            continue
        if chunk_type == "content_block_stop":
            idx = int(chunk.get("index", -1))
            meta = pending_tool.pop(idx, None)
            if meta is None:
                continue
            joined = "".join(partial_input.pop(idx, [])).strip()
            input_payload = meta["input"]
            if joined:
                try:
                    import json as _json

                    input_payload = _json.loads(joined)
                except Exception:  # noqa: BLE001
                    input_payload = {"_raw": joined}
            state.add_event(
                "tool.invoke",
                {
                    "tool_use_id": meta["tool_use_id"],
                    "name": meta["name"],
                    "input": input_payload,
                },
            )
            continue
        # Unknown chunk types: keep silent. The upstream method does
        # the same; we don't want to drown the event bus.

    if response is None:
        raise APIError(
            "Stream ended without message_complete",
            category=ErrorCategory.UNKNOWN,
            code=ExecutorErrorCode.EXEC_API_STREAM_INCOMPLETE,
        )
    return response


_APPLIED = False


def apply_executor_patches() -> None:
    """Install the streaming patch. Idempotent."""
    global _APPLIED  # noqa: PLW0603
    if _APPLIED:
        return
    APIStage._call_streaming = _patched_call_streaming  # type: ignore[assignment]
    _APPLIED = True
