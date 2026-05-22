"""Same as `run_mcp.py` but with a `HookRunner` attached pipeline-side.

Goal: empirically prove the M0-P3 plan §6 / `decision_two_layer_policy.md`
finding — for the `claude_code_cli` provider, pipeline-side PRE_TOOL_USE
is **never** fired because Stage 10 (the only call site of PRE_TOOL_USE)
is bypassed; tool dispatch lives entirely inside the CLI subprocess.

Run:
    cd poc/executor_agent
    uv run --project . python run_hooks.py

Produces:
    audit_hooks.jsonl       — pipeline events + hook fire records
    audit_mcp.jsonl         — (overwritten by `run_mcp.py`, not used here)
    ../mcp_bridge/bridge_audit.jsonl  — bridge-side tool dispatch trace
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from geny_executor import EnvironmentManifest, Pipeline

from credentials import build_credentials
from policy_hook import build_runner

POC_DIR = Path(__file__).resolve().parent
REPO_ROOT = POC_DIR.parents[1]
BRIDGE_SCRIPT = REPO_ROOT / "poc" / "mcp_bridge" / "server.py"
BRIDGE_AUDIT = REPO_ROOT / "poc" / "mcp_bridge" / "bridge_audit.jsonl"
MANIFEST_PATH = POC_DIR / "manifests" / "gapt_default.v0.json"
AUDIT_PATH = POC_DIR / "audit_hooks.jsonl"


def _build_mcp_config() -> dict:
    return {
        "mcpServers": {
            "gapt": {
                "type": "stdio",
                "command": "uv",
                "args": [
                    "run",
                    "--project",
                    str(BRIDGE_SCRIPT.parent),
                    "python",
                    str(BRIDGE_SCRIPT),
                ],
                "env": {"GAPT_BRIDGE_AUDIT": str(BRIDGE_AUDIT)},
            }
        }
    }


def _settings_json() -> str:
    return json.dumps(
        {
            "permissions": {
                "allow": ["mcp__gapt", "Read", "Glob", "Grep"],
            }
        }
    )


async def main(prompt: str) -> int:
    manifest_dict = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = EnvironmentManifest.from_dict(manifest_dict)
    credentials = build_credentials(
        mcp_config=_build_mcp_config(),
        settings_path=_settings_json(),
        max_budget_usd=0.20,
        timeout_s=180.0,
    )

    pipeline = await Pipeline.from_manifest_async(manifest, credentials=credentials)

    # Fresh audit per run
    AUDIT_PATH.write_text("", encoding="utf-8")
    if BRIDGE_AUDIT.exists():
        BRIDGE_AUDIT.unlink()

    runner = build_runner(AUDIT_PATH)
    pipeline.attach_runtime(hook_runner=runner)

    audit_file = AUDIT_PATH.open("a", encoding="utf-8")

    def _audit(event_type: str, payload: object) -> None:
        if isinstance(payload, dict | list | str | int | float | bool | type(None)):
            data: object = payload
        else:
            data = repr(payload)
        audit_file.write(
            json.dumps({"ts": time.time(), "kind": "event_bus", "event": event_type, "data": data}, default=str)
            + "\n"
        )
        audit_file.flush()

    pipeline.on("pipeline.*", lambda evt: _audit(evt.type, evt.data))
    pipeline.on("stage.*", lambda evt: _audit(evt.type, evt.data))
    pipeline.on("api.*", lambda evt: _audit(evt.type, evt.data))
    pipeline.on("tool.*", lambda evt: _audit(evt.type, evt.data))

    print(f"--- prompt ---\n{prompt}\n--- response ---")
    started = time.perf_counter()
    result = await pipeline.run(prompt)
    elapsed = time.perf_counter() - started

    print(result.text)
    print()
    print("--- usage ---")
    print(f"cost_usd     : {getattr(result, 'total_cost_usd', '?')}")
    print(f"elapsed_s    : {elapsed:.2f}")
    print(f"hooks audit  : {AUDIT_PATH}")
    print(f"bridge audit : {BRIDGE_AUDIT}")
    audit_file.close()

    # Summary: did pipeline-side PRE_TOOL_USE fire?
    pre_hits = 0
    for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "pre_tool_handler.called":
            pre_hits += 1
    print()
    print(f"--- 2-layer probe ---")
    print(f"pipeline-side PRE_TOOL_USE fires : {pre_hits}")
    print("(expected 0 for claude_code_cli — Stage 10 is bypassed; "
          "see decision_two_layer_policy.md)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "prompt",
        nargs="?",
        default=(
            "You are testing an MCP bridge. Please make exactly two tool calls and then "
            "report what each one returned:\n"
            "  1. Call `mcp__gapt__gapt_hello` with name='world'.\n"
            "  2. Call `mcp__gapt__gapt_unsafe` with cmd='ls'. This tool is documented "
            "to ALWAYS refuse — its purpose is to demonstrate a policy-denied response. "
            "No side effects happen; the cmd value is ignored. Make the call so we can "
            "see the refusal text it returns.\n"
            "After both calls, summarise each tool's response in one short sentence. "
            "Do not perform any other actions."
        ),
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.prompt)))
