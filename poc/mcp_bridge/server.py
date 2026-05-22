"""MCP stdio bridge for the M0-P3 PoC.

Spawned by the `claude` CLI via `extras["mcp_config"]`; surfaces this
PoC's tool catalogue to the CLI's LLM as `mcp__gapt__<tool>`. Tool
dispatch is inline here (single-process PoC) — M1-E2 splits it into a
proper host RPC.

Tools:
- `gapt_hello(name: str) -> str` — friendly echo, the happy path.
- `gapt_unsafe(cmd: str) -> str` — every call is denied by an in-server
  policy hook; lets the CLI see how a `tool_result.isError = true` flow
  looks from its side.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

AUDIT_PATH = Path(
    os.environ.get(
        "GAPT_BRIDGE_AUDIT", str(Path(__file__).resolve().parent / "bridge_audit.jsonl")
    )
)


def _audit(record: dict[str, Any]) -> None:
    record = {"ts": time.time(), **record}
    AUDIT_PATH.write_text("", encoding="utf-8") if not AUDIT_PATH.exists() else None
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


server = Server("gapt-poc")


@server.list_tools()
async def list_tools() -> list[Tool]:
    _audit({"event": "tools/list"})
    return [
        Tool(
            name="gapt_hello",
            description="Friendly echo — the happy-path tool. Use to greet someone by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Who to greet."},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="gapt_unsafe",
            description=(
                "Intentionally policy-denied tool. Every call returns isError=true so the "
                "LLM can demonstrate handling of denied tool calls."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Anything; will be refused."},
                },
                "required": ["cmd"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    _audit({"event": "tools/call", "name": name, "args_keys": sorted(arguments)})

    if name == "gapt_hello":
        who = str(arguments.get("name", "world"))
        text = f"hello, {who} — from gapt poc bridge"
        _audit({"event": "tools/call.ok", "name": name})
        return [TextContent(type="text", text=text)]

    if name == "gapt_unsafe":
        _audit({"event": "tools/call.denied", "name": name, "code": "exec.tool.access_denied"})
        # In the MCP protocol, returning isError=true happens via the
        # framework when we raise — but the cleanest way for the LLM to
        # see a structured refusal is to return a plain-text explanation
        # and let it react. PR4 will wire this through a HookRunner so
        # the same denial flows through pipeline-side audit too.
        return [
            TextContent(
                type="text",
                text=(
                    "Policy denied (exec.tool.access_denied): gapt_unsafe is a "
                    "PoC tool that always refuses. No state changed."
                ),
            )
        ]

    _audit({"event": "tools/call.unknown", "name": name})
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
