"""DaemonClient — list_tools / call_tool against a fake aiohttp daemon.

We spin up a real aiohttp app on a temp unix socket so the client's
``httpx.AsyncHTTPTransport(uds=...)`` path is exercised end-to-end.
"""

from __future__ import annotations

import os
from pathlib import Path  # noqa: TC003  — pytest fixture annotation
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from aiohttp import web

from gapt_runtime.mcp_bridge.client import DaemonClient, DaemonClientError
from gapt_runtime.mcp_bridge.server import (
    _audit,
    _audit_path,
    _build_client_from_env,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ─────────────────────────────────────────────── fake daemon ──


def _make_app(*, behavior: str = "ok", token: str = "bridge-token") -> web.Application:
    """Build a tiny aiohttp app that simulates the daemon.

    behavior selects:
    - ``ok``                — normal responses
    - ``unauthorized``      — 401 for every call
    - ``server_error``      — 500 for every call
    - ``unknown_tool``      — 404 on tools/call
    - ``policy_denied``     — 200 with ok=false
    - ``malformed_list``    — tools/list returns a dict instead of list
    """

    async def handle_list(request: web.Request) -> web.Response:
        if behavior == "unauthorized":
            return web.Response(status=401, text="bad jwt")
        if behavior == "server_error":
            return web.Response(status=500, text="boom")
        if behavior == "malformed_list":
            return web.json_response({"tools": {"oops": "not-a-list"}})
        return web.json_response({"tools": [{"name": "gapt_hello", "description": "say hi"}]})

    async def handle_call(request: web.Request) -> web.Response:
        if behavior == "unauthorized":
            return web.Response(status=401)
        if behavior == "server_error":
            return web.Response(status=500, text="boom")
        if behavior == "unknown_tool":
            return web.Response(status=404)
        if behavior == "policy_denied":
            return web.json_response(
                {
                    "ok": False,
                    "error": {
                        "code": "exec.tool.access_denied",
                        "message": "policy refused gapt_unsafe",
                    },
                }
            )
        return web.json_response({"ok": True, "result": "hello world"})

    app = web.Application()
    app.router.add_get("/tools/list", handle_list)
    app.router.add_post("/tools/call", handle_call)
    return app


@pytest_asyncio.fixture
async def daemon_socket(tmp_path: Path, request: pytest.FixtureRequest) -> AsyncIterator[Path]:
    behavior = getattr(request, "param", "ok")
    sock = tmp_path / "agent.sock"
    app = _make_app(behavior=behavior)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, str(sock))
    await site.start()
    try:
        yield sock
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("daemon_socket", ["ok"], indirect=True)
async def test_list_tools_happy_path(daemon_socket: Path) -> None:
    client = DaemonClient(socket_path=str(daemon_socket), token="t")
    tools = await client.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "gapt_hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("daemon_socket", ["ok"], indirect=True)
async def test_call_tool_happy_path(daemon_socket: Path) -> None:
    client = DaemonClient(socket_path=str(daemon_socket), token="t")
    response = await client.call_tool(name="gapt_hello", arguments={"name": "world"})
    assert response["ok"] is True
    assert response["result"] == "hello world"


@pytest.mark.asyncio
@pytest.mark.parametrize("daemon_socket", ["unauthorized"], indirect=True)
async def test_unauthorized_maps_to_transport_error(daemon_socket: Path) -> None:
    client = DaemonClient(socket_path=str(daemon_socket), token="bogus", retries=0)
    with pytest.raises(DaemonClientError) as exc:
        await client.list_tools()
    assert exc.value.code == "exec.tool.transport"
    assert "401" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("daemon_socket", ["server_error"], indirect=True)
async def test_server_error_maps_to_transport_error(daemon_socket: Path) -> None:
    client = DaemonClient(socket_path=str(daemon_socket), token="t", retries=0)
    with pytest.raises(DaemonClientError) as exc:
        await client.call_tool(name="x", arguments={})
    assert exc.value.code == "exec.tool.transport"


@pytest.mark.asyncio
@pytest.mark.parametrize("daemon_socket", ["unknown_tool"], indirect=True)
async def test_call_tool_unknown_is_payload_not_exception(daemon_socket: Path) -> None:
    client = DaemonClient(socket_path=str(daemon_socket), token="t")
    response = await client.call_tool(name="missing", arguments={})
    assert response["ok"] is False
    assert response["error"]["code"] == "exec.tool.unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("daemon_socket", ["policy_denied"], indirect=True)
async def test_call_tool_policy_denied_carries_code(daemon_socket: Path) -> None:
    client = DaemonClient(socket_path=str(daemon_socket), token="t")
    response = await client.call_tool(name="gapt_unsafe", arguments={"cmd": "ls"})
    assert response["ok"] is False
    assert response["error"]["code"] == "exec.tool.access_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("daemon_socket", ["malformed_list"], indirect=True)
async def test_malformed_list_raises_transport(daemon_socket: Path) -> None:
    client = DaemonClient(socket_path=str(daemon_socket), token="t", retries=0)
    with pytest.raises(DaemonClientError) as exc:
        await client.list_tools()
    assert exc.value.code == "exec.tool.transport"


@pytest.mark.asyncio
async def test_dead_socket_retries_then_raises(tmp_path: Path) -> None:
    # No daemon listening on this path → connection refused every time.
    sock = tmp_path / "nobody.sock"
    client = DaemonClient(socket_path=str(sock), token="t", retries=1, timeout_s=1.0)
    with pytest.raises(DaemonClientError) as exc:
        await client.list_tools()
    assert exc.value.code == "exec.tool.transport"
    assert "attempts" in str(exc.value)


def test_build_server_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GAPT_BRIDGE_DAEMON_SOCK", raising=False)
    monkeypatch.delenv("GAPT_BRIDGE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GAPT_BRIDGE_DAEMON_SOCK"):
        _build_client_from_env()
    monkeypatch.setenv("GAPT_BRIDGE_DAEMON_SOCK", "/tmp/x.sock")
    with pytest.raises(RuntimeError, match="GAPT_BRIDGE_TOKEN"):
        _build_client_from_env()
    monkeypatch.setenv("GAPT_BRIDGE_TOKEN", "tok")
    client = _build_client_from_env()
    assert client.socket_path == "/tmp/x.sock"
    assert client.token == "tok"


def test_build_server_audit_path_optional(tmp_path: Path) -> None:
    # No env → audit_path is None, _audit is a no-op.
    os.environ.pop("GAPT_BRIDGE_AUDIT", None)
    assert _audit_path() is None
    _audit({"event": "noop"})

    # With env → file gets written.
    audit = tmp_path / "bridge_audit.jsonl"
    os.environ["GAPT_BRIDGE_AUDIT"] = str(audit)
    try:
        _audit({"event": "write"})
        assert audit.exists()
        assert "write" in audit.read_text()
    finally:
        os.environ.pop("GAPT_BRIDGE_AUDIT", None)
