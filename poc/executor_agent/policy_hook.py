"""HookRunner-based policy demo for M0-P3 PR4.

Wires a `geny_executor.hooks.HookRunner` into the pipeline so we can answer
the core M0-P3 design question empirically:

    *When the `claude_code_cli` provider runs the agentic loop INSIDE the CLI
    subprocess, do pipeline-side PRE_TOOL_USE hooks ever fire?*

Answer (see `decision_two_layer_policy.md`): **no**. PRE_TOOL_USE is fired
by Stage 10 only, and Stage 10 is bypassed for `claude_code_cli`. The
pipeline-side hook is silent even when the CLI's LLM dispatches two tools
via MCP. The denial of `gapt_unsafe` instead comes from the bridge's own
in-process policy hook — that is "Layer 2".

This module exposes:

- `build_runner(audit_path)` — returns a configured `HookRunner` with
  an in-process PRE_TOOL_USE handler that *would* deny `gapt_unsafe` if
  given the chance, plus an audit callback that appends every fired hook
  to a JSONL file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from geny_executor.hooks import (
    HookConfig,
    HookEvent,
    HookEventPayload,
    HookOutcome,
    HookRunner,
)


def _now() -> float:
    return time.time()


def _build_pre_tool_handler(audit_path: Path):
    """In-process PRE_TOOL_USE handler. Denies the PoC's `gapt_unsafe` tool.

    With `claude_code_cli`, this handler is registered but never reached for
    CLI-internal tool dispatch. It IS reached if the same manifest is run
    against a direct-API provider that goes through Stage 10. PR4 proves
    the first half; the second half is a future-cycle exercise.
    """

    async def handler(payload: HookEventPayload) -> HookOutcome:
        record = {
            "ts": _now(),
            "kind": "pre_tool_handler.called",
            "tool_name": payload.tool_name,
            "tool_input_keys": sorted((payload.tool_input or {}).keys()),
        }
        audit_path.open("a", encoding="utf-8").write(json.dumps(record) + "\n")

        if payload.tool_name == "gapt_unsafe":
            return HookOutcome(
                decision="deny",
                stop_reason="PoC layer-1 policy: gapt_unsafe is denied at pipeline gate.",
            )
        return HookOutcome()

    return handler


def build_runner(audit_path: Path) -> HookRunner:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    config = HookConfig(enabled=True)
    runner = HookRunner(config)
    runner.register_in_process(HookEvent.PRE_TOOL_USE, _build_pre_tool_handler(audit_path))

    async def audit_cb(record: dict[str, Any]) -> None:
        record = {"ts": _now(), "kind": "hook_runner.audit", **record}
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

    runner.set_audit_callback(audit_cb)
    return runner
