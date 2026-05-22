"""End-to-end smoke for M0-P3 PR2.

Loads gapt_default.v0.json, builds a CredentialBundle that picks up the
host's existing Claude OAuth subscription (or ANTHROPIC_API_KEY), boots
the pipeline, sends one message, and prints the assistant's reply plus
a usage summary.
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
MANIFEST_PATH = POC_DIR / "manifests" / "gapt_default.v0.json"
AUDIT_PATH = POC_DIR / "audit.jsonl"


async def main(prompt: str) -> int:
    manifest_dict = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = EnvironmentManifest.from_dict(manifest_dict)
    credentials = build_credentials()

    pipeline = await Pipeline.from_manifest_async(manifest, credentials=credentials)

    # Stream every event to a JSONL audit log; we'll lean on this in PR4/PR5.
    audit_file = AUDIT_PATH.open("a", encoding="utf-8")
    def _audit(event_type: str, payload: object) -> None:
        if isinstance(payload, dict | list | str | int | float | bool | type(None)):
            data: object = payload
        else:
            data = repr(payload)
        record = {"ts": time.time(), "event": event_type, "data": data}
        audit_file.write(json.dumps(record, default=str) + "\n")
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
    usage = getattr(result, "usage", None) or {}
    cost = getattr(result, "total_cost_usd", None)
    print(f"input_tokens : {getattr(usage, 'input_tokens', '?')}")
    print(f"output_tokens: {getattr(usage, 'output_tokens', '?')}")
    print(f"cost_usd     : {cost if cost is not None else '?'}")
    print(f"elapsed_s    : {elapsed:.2f}")
    print(f"audit_jsonl  : {AUDIT_PATH}")
    audit_file.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Hello! What's 2 + 2? Reply in one short sentence.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.prompt)))
