"""POST_TOOL_USE accumulator — rolls up token / duration / cost.

The accumulator is a tiny in-memory tally; Cycle 2.10's SSE layer
takes the supplied callback and surfaces ``event: cost`` pushes with
~1 s debounce.

`HookEventPayload.details` is the geny-executor's free-form dict
attached to per-stage events. The Stage 7 (token) emitter populates
``input_tokens`` / ``output_tokens`` / ``cost_usd``; the Stage 10
(tool) emitter populates ``duration_ms``. We sample whatever the
pipeline gave us — missing keys are zero.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from geny_executor.hooks import HookEventPayload, HookOutcome

logger = structlog.get_logger(__name__)


CostHookHandler = Callable[[HookEventPayload], Awaitable[HookOutcome]]
CostCallback = Callable[["CostAccumulator"], Awaitable[None]]


@dataclass
class CostAccumulator:
    """Running totals for one agent session."""

    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    # Phase K.2 — explicit Anthropic cache token tracking. The cost
    # already reflects these (Phase I.3's pricing-fallback computes
    # them off the executor's `cache_write` / `cache_read` payload
    # fields); these counters surface the counts so the UI can say
    # "you paid $0.013 because 3400 cache_write tokens were primed".
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    tool_duration_ms: int = 0
    # Per-tool counts, useful for SSE summaries.
    by_tool: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "tool_calls": self.tool_calls,
            "tool_duration_ms": self.tool_duration_ms,
            "by_tool": dict(self.by_tool),
        }


def build_cost_hook(
    *,
    accumulator: CostAccumulator,
    on_update: CostCallback | None = None,
) -> CostHookHandler:
    """Return a ``POST_TOOL_USE`` handler that accumulates + invokes
    ``on_update`` (if supplied) with the snapshot. The callback is
    awaited synchronously by the pipeline; debouncing is the
    SSE layer's job (Cycle 2.10).
    """

    async def handler(payload: HookEventPayload) -> HookOutcome:
        details = payload.details or {}
        accumulator.tool_calls += 1
        if payload.tool_name:
            accumulator.by_tool[payload.tool_name] = (
                accumulator.by_tool.get(payload.tool_name, 0) + 1
            )
        if "duration_ms" in details and isinstance(details["duration_ms"], int):
            accumulator.tool_duration_ms += details["duration_ms"]
        if "input_tokens" in details and isinstance(details["input_tokens"], int):
            accumulator.input_tokens += details["input_tokens"]
        if "output_tokens" in details and isinstance(details["output_tokens"], int):
            accumulator.output_tokens += details["output_tokens"]
        if "cost_usd" in details and isinstance(details["cost_usd"], int | float):
            accumulator.cost_usd += float(details["cost_usd"])

        if on_update is not None:
            await on_update(accumulator)
        return HookOutcome.passthrough()

    return handler
