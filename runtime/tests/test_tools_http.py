"""HTTP integration — /tools/list + /tools/call through the real
aiohttp test server + JWT middleware."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import jwt
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from gapt_runtime.auth import AUDIENCE, ISSUER
from gapt_runtime.daemon import create_app
from gapt_runtime.settings import DaemonSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

JWT_SECRET = "test-daemon-secret-with-32-plus-characters"
SESSION_ID = "01KS900000000000000000SESS"


def _mint_token() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": SESSION_ID,
            "iat": now,
            "exp": now + 60,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_mint_token()}"}


@pytest_asyncio.fixture
async def daemon(tmp_path: Path) -> AsyncIterator[tuple[TestClient, Path]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = DaemonSettings(
        socket_path=Path("/run/agent.sock"),
        jwt_secret=JWT_SECRET,
        project_id="01KS900000000000000000PRJX",
        workspace_id="01KS900000000000000000WSP1",
        session_id=SESSION_ID,
        workspace_root=workspace,
    )
    app = create_app(settings)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client, workspace
    finally:
        await client.close()


# ─────────────────────────────────────────────────── auth gate ──


@pytest.mark.asyncio
async def test_tools_list_requires_token(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    unauthed = await client.get("/tools/list")
    assert unauthed.status == 401


@pytest.mark.asyncio
async def test_tools_call_requires_token(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    unauthed = await client.post("/tools/call", json={"name": "gapt_read", "arguments": {}})
    assert unauthed.status == 401


# ──────────────────────────────────────────────────── /tools/list ──


@pytest.mark.asyncio
async def test_tools_list_returns_registered_tools(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.get("/tools/list", headers=_auth())
    assert resp.status == 200
    payload = await resp.json()
    names = sorted(t["name"] for t in payload["tools"])
    # gapt_git + gapt_pr joined the registry in Cycle 2.7.
    assert names == [
        "gapt_edit",
        "gapt_git",
        "gapt_glob",
        "gapt_grep",
        "gapt_pr",
        "gapt_read",
    ]
    for tool in payload["tools"]:
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"


# ──────────────────────────────────────────────────── /tools/call ──


@pytest.mark.asyncio
async def test_call_gapt_read_happy(daemon: tuple[TestClient, Path]) -> None:
    client, workspace = daemon
    (workspace / "hi.txt").write_text("alpha\nbeta\n")
    resp = await client.post(
        "/tools/call",
        json={"name": "gapt_read", "arguments": {"path": "hi.txt"}},
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["result"] == "alpha\nbeta"
    assert body["metadata"]["total_lines"] == 2


@pytest.mark.asyncio
async def test_call_gapt_glob_happy(daemon: tuple[TestClient, Path]) -> None:
    client, workspace = daemon
    (workspace / "src").mkdir()
    (workspace / "src" / "a.py").write_text("")
    (workspace / "src" / "b.py").write_text("")
    resp = await client.post(
        "/tools/call",
        json={"name": "gapt_glob", "arguments": {"pattern": "src/*.py"}},
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert "src/a.py" in body["result"]
    assert "src/b.py" in body["result"]


@pytest.mark.asyncio
async def test_call_gapt_grep_happy(daemon: tuple[TestClient, Path]) -> None:
    client, workspace = daemon
    (workspace / "main.py").write_text("def foo():\n    return 1\n")
    resp = await client.post(
        "/tools/call",
        json={"name": "gapt_grep", "arguments": {"pattern": "foo"}},
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert "main.py:1:" in body["result"]
    assert body["metadata"]["match_count"] == 1


@pytest.mark.asyncio
async def test_call_gapt_edit_happy(daemon: tuple[TestClient, Path]) -> None:
    client, workspace = daemon
    (workspace / "f.py").write_text("x = 1\n")
    resp = await client.post(
        "/tools/call",
        json={
            "name": "gapt_edit",
            "arguments": {"path": "f.py", "old": "x = 1", "new": "x = 42"},
        },
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert (workspace / "f.py").read_text() == "x = 42\n"


@pytest.mark.asyncio
async def test_call_unknown_tool_404(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.post(
        "/tools/call",
        json={"name": "gapt_doesnt_exist", "arguments": {}},
        headers=_auth(),
    )
    assert resp.status == 404
    body = await resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "exec.tool.unknown"


@pytest.mark.asyncio
async def test_call_invalid_payload_400(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.post(
        "/tools/call",
        data="not-json",
        headers={**_auth(), "Content-Type": "application/json"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_call_tool_error_returns_ok_false(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    # gapt_read with a path that escapes the workspace.
    resp = await client.post(
        "/tools/call",
        json={"name": "gapt_read", "arguments": {"path": "../../etc/passwd"}},
        headers=_auth(),
    )
    # Domain refusals are 200 with ok=false so the MCP bridge can
    # format isError=true cleanly per Cycle 2.3 contract.
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "exec.tool.access_denied"


@pytest.mark.asyncio
async def test_call_missing_name_400(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.post(
        "/tools/call",
        json={"arguments": {}},
        headers=_auth(),
    )
    assert resp.status == 400
