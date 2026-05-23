"""MCP stdio server — talks to the CLI on stdin/stdout, to the
daemon over a unix socket.

Environment variables read at startup:
- ``GAPT_BRIDGE_DAEMON_SOCK`` — path to the daemon's unix socket
  (typically ``/run/agent.sock``). Required.
- ``GAPT_BRIDGE_TOKEN`` — short-lived JWT minted by the control plane
  when it spawned the sandbox; the daemon validates it on every
  request. Required.
- ``GAPT_BRIDGE_AUDIT`` — optional JSONL file where each MCP turn is
  logged for debugging. Defaults off.
- ``GAPT_BRIDGE_TIMEOUT_S`` — per-RPC timeout (default 10s).
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

from gapt_runtime.mcp_bridge.client import DaemonClient, DaemonClientError


def _audit_path() -> Path | None:
    raw = os.environ.get("GAPT_BRIDGE_AUDIT")
    return Path(raw) if raw else None


def _audit(record: dict[str, Any]) -> None:
    path = _audit_path()
    if path is None:
        return
    record = {"ts": time.time(), **record}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_client_from_env() -> DaemonClient:
    socket_path = os.environ.get("GAPT_BRIDGE_DAEMON_SOCK")
    if not socket_path:
        raise RuntimeError(
            "GAPT_BRIDGE_DAEMON_SOCK is not set; the MCP bridge must be "
            "spawned by the GAPT control plane."
        )
    token = os.environ.get("GAPT_BRIDGE_TOKEN")
    if not token:
        raise RuntimeError(
            "GAPT_BRIDGE_TOKEN is not set; refusing to talk to the daemon without an auth token."
        )
    timeout_s = float(os.environ.get("GAPT_BRIDGE_TIMEOUT_S", "10.0"))
    return DaemonClient(socket_path=socket_path, token=token, timeout_s=timeout_s)


def build_server(*, daemon: DaemonClient | None = None) -> Server:
    """Wire up the MCP server's tools/list and tools/call handlers."""
    server = Server("gapt")
    client = daemon or _build_client_from_env()

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        _audit({"event": "tools/list"})
        try:
            tools = await client.list_tools()
        except DaemonClientError as exc:
            _audit({"event": "tools/list.transport_error", "code": exc.code})
            # On list errors return an empty palette — the CLI will
            # surface a normal "no tools" state instead of crashing.
            return []
        return [
            Tool(
                name=t["name"],
                description=t.get("description", ""),
                inputSchema=t.get("input_schema", {"type": "object"}),
            )
            for t in tools
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        _audit({"event": "tools/call", "name": name, "args_keys": sorted(arguments)})
        try:
            response = await client.call_tool(name=name, arguments=arguments)
        except DaemonClientError as exc:
            _audit(
                {
                    "event": "tools/call.transport_error",
                    "name": name,
                    "code": exc.code,
                }
            )
            return [
                TextContent(
                    type="text",
                    text=f"Transport error ({exc.code}): {exc}",
                )
            ]
        if response.get("ok"):
            payload = response.get("result", "")
            text = payload if isinstance(payload, str) else json.dumps(payload)
            _audit({"event": "tools/call.ok", "name": name})
            return [TextContent(type="text", text=text)]

        error = response.get("error") or {}
        code = error.get("code", "exec.tool.unknown")
        message = error.get("message", "tool dispatch failed")
        _audit(
            {
                "event": "tools/call.denied",
                "name": name,
                "code": code,
            }
        )
        return [
            TextContent(
                type="text",
                text=f"{code}: {message}",
            )
        ]

    return server


async def _serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
