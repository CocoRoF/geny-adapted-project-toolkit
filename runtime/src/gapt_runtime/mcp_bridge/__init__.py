"""MCP stdio bridge — promoted from M0-P3's ``poc/mcp_bridge/``.

Spawned by the agent CLI through its ``extras["mcp_config"]``. Speaks
the Model Context Protocol with the CLI's LLM over stdin/stdout; on
the host side it forwards every ``tools/list`` and ``tools/call`` to
the in-sandbox daemon (``GAPT_BRIDGE_DAEMON_SOCK``) authenticated by
a short-lived JWT (``GAPT_BRIDGE_TOKEN``).

Why a bridge instead of letting the CLI talk to the daemon directly:
- The CLI only knows MCP. The daemon only knows HTTP-over-unix-socket.
- The bridge owns timeout / retry / `exec.tool.transport` error
  mapping, so the CLI sees a clean MCP refusal instead of an
  HTTP 5xx.
- Tool policy decisions live behind the daemon (which forwards to
  the control plane). The bridge is policy-blind on purpose.
"""

from gapt_runtime.mcp_bridge.client import DaemonClient, DaemonClientError
from gapt_runtime.mcp_bridge.server import build_server, main

__all__ = ["DaemonClient", "DaemonClientError", "build_server", "main"]
