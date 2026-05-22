"""Same as run.py but with the MCP stdio bridge from poc/mcp_bridge attached.

The CLI's LLM sees `mcp__gapt__gapt_hello` and `mcp__gapt__gapt_unsafe`
on top of its built-in palette. The default prompt asks it to exercise
both paths so the audit log captures one successful tool call and one
policy-denied one.
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

POC_DIR = Path(__file__).resolve().parent
REPO_ROOT = POC_DIR.parents[1]
BRIDGE_SCRIPT = REPO_ROOT / "poc" / "mcp_bridge" / "server.py"
BRIDGE_AUDIT = REPO_ROOT / "poc" / "mcp_bridge" / "bridge_audit.jsonl"
MANIFEST_PATH = POC_DIR / "manifests" / "gapt_default.v0.json"
AUDIT_PATH = POC_DIR / "audit_mcp.jsonl"


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
    # Allow this PoC's MCP surface + a couple of harmless built-ins so
    # the CLI doesn't refuse simple read-only intents.
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

    audit_file = AUDIT_PATH.open("w", encoding="utf-8")

    def _audit(event_type: str, payload: object) -> None:
        if isinstance(payload, dict | list | str | int | float | bool | type(None)):
            data: object = payload
        else:
            data = repr(payload)
        audit_file.write(
            json.dumps({"ts": time.time(), "event": event_type, "data": data}, default=str)
            + "\n"
        )
        audit_file.flush()

    pipeline.on("pipeline.*", lambda evt: _audit(evt.type, evt.data))
    pipeline.on("stage.*", lambda evt: _audit(evt.type, evt.data))
    pipeline.on("api.*", lambda evt: _audit(evt.type, evt.data))
    pipeline.on("tool.*", lambda evt: _audit(evt.type, evt.data))

    # Clear the bridge's audit too so each run is fresh
    if BRIDGE_AUDIT.exists():
        BRIDGE_AUDIT.unlink()

    print(f"--- prompt ---\n{prompt}\n--- response ---")
    started = time.perf_counter()
    result = await pipeline.run(prompt)
    elapsed = time.perf_counter() - started

    print(result.text)
    print()
    print("--- usage ---")
    print(f"cost_usd     : {getattr(result, 'total_cost_usd', '?')}")
    print(f"elapsed_s    : {elapsed:.2f}")
    print(f"pipeline aud : {AUDIT_PATH}")
    print(f"bridge   aud : {BRIDGE_AUDIT}")
    audit_file.close()
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
