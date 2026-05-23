"""HTTP-over-unix-socket client the bridge uses to reach the daemon.

The daemon's HTTP API exposes `/tools/list` + `/tools/call` (added in
Cycle 2.4). This client is intentionally tiny so the bridge can stay
stateless: connect per RPC, retry once on transport failure, map
everything else into ``DaemonClientError`` so the MCP layer can build
a clean ``isError=true`` response without leaking HTTP internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


_TOOLS_LIST_PATH = "/tools/list"
_TOOLS_CALL_PATH = "/tools/call"


class DaemonClientError(RuntimeError):
    """Transport-level failure. Maps to MCP ``isError=true`` with the
    error code echoed into the response text so the CLI's LLM can see
    `exec.tool.transport`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class DaemonClient:
    socket_path: str  # /run/agent.sock or test override
    token: str
    timeout_s: float = 10.0
    retries: int = 1

    def _base_url(self) -> str:
        # httpx supports unix transports via the `transport=` arg; the URL
        # host is irrelevant once the transport is set. Use http+unix
        # convention so logs are scannable.
        return "http://daemon"

    def _make_client(self) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(uds=self.socket_path)
        return httpx.AsyncClient(
            transport=transport,
            timeout=self.timeout_s,
            base_url=self._base_url(),
            headers={"Authorization": f"Bearer {self.token}"},
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        last_error: BaseException | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self._make_client() as client:
                    response = await client.get(_TOOLS_LIST_PATH)
                if response.status_code == 401:
                    raise DaemonClientError(
                        "exec.tool.transport",
                        "daemon rejected bridge JWT (401)",
                    )
                if response.status_code >= 400:
                    raise DaemonClientError(
                        "exec.tool.transport",
                        f"daemon /tools/list returned {response.status_code}: "
                        f"{response.text[:200]}",
                    )
                payload = response.json()
                tools = payload.get("tools", [])
                if not isinstance(tools, list):
                    raise DaemonClientError(
                        "exec.tool.transport",
                        f"daemon /tools/list returned non-list: {type(tools).__name__}",
                    )
                return tools
            except DaemonClientError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                logger.warning(
                    "mcp_bridge.list_tools.retry",
                    attempt=attempt,
                    error=str(exc)[:200],
                )
        raise DaemonClientError(
            "exec.tool.transport",
            f"daemon unreachable after {self.retries + 1} attempts: {last_error!r}",
        )

    async def call_tool(self, *, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool on the daemon. The daemon's response shape is
        ``{"ok": bool, "result"?: ..., "error"?: {"code": str, "message": str}}``.
        Transport errors raise; *policy* denials come back as a normal
        response with ``ok=false`` so the MCP layer can format
        ``isError=true``.
        """
        body = {"name": name, "arguments": arguments}
        last_error: BaseException | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self._make_client() as client:
                    response = await client.post(_TOOLS_CALL_PATH, json=body)
                if response.status_code == 401:
                    raise DaemonClientError(
                        "exec.tool.transport",
                        "daemon rejected bridge JWT (401)",
                    )
                if response.status_code == 404:
                    return {
                        "ok": False,
                        "error": {
                            "code": "exec.tool.unknown",
                            "message": f"tool {name!r} not registered on daemon",
                        },
                    }
                if response.status_code >= 500:
                    raise DaemonClientError(
                        "exec.tool.transport",
                        f"daemon /tools/call returned {response.status_code}: "
                        f"{response.text[:200]}",
                    )
                return response.json()  # type: ignore[no-any-return]
            except DaemonClientError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                logger.warning(
                    "mcp_bridge.call_tool.retry",
                    attempt=attempt,
                    error=str(exc)[:200],
                )
        raise DaemonClientError(
            "exec.tool.transport",
            f"daemon unreachable after {self.retries + 1} attempts: {last_error!r}",
        )
