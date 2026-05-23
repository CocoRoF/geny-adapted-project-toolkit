"""PTY handlers — /open_pty + /pty/{id} WebSocket bridge.

These tests really do fork a shell so they need a host with `/bin/sh`
available. CI's ubuntu image qualifies. The PtyManager runs in the
test event loop; reading from the pty master is wired via
`loop.add_reader` so the test stays fully async.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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

JWT_SECRET = "pty-test-secret-32-chars-long-enough"
SESSION_ID = "01KS900000000000000000SESS"


def _mint() -> str:
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
    return {"Authorization": f"Bearer {_mint()}"}


@pytest_asyncio.fixture
async def daemon(tmp_path: Path) -> AsyncIterator[TestClient]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = DaemonSettings(
        socket_path=Path("/run/agent.sock"),
        jwt_secret=JWT_SECRET,
        project_id="p",
        workspace_id="w",
        session_id=SESSION_ID,
        workspace_root=workspace,
    )
    app = create_app(settings)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_open_pty_returns_id_pid_size(daemon: TestClient) -> None:
    resp = await daemon.post(
        "/open_pty",
        json={"shell": "/bin/sh", "rows": 30, "cols": 100},
        headers=_auth(),
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["rows"] == 30
    assert body["cols"] == 100
    assert isinstance(body["pid"], int)
    # Cleanup so the test process doesn't leak forks.
    await daemon.post(f"/pty/{body['id']}/close", headers=_auth())


@pytest.mark.asyncio
async def test_ws_echoes_shell_output(daemon: TestClient) -> None:
    open_resp = await daemon.post(
        "/open_pty",
        json={"shell": "/bin/sh"},
        headers=_auth(),
    )
    session_id = (await open_resp.json())["id"]

    async with daemon.ws_connect(f"/pty/{session_id}", headers=_auth()) as ws:
        # Drain whatever the shell prints at startup (PS1 banner, etc.).
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ws.receive(), timeout=1.0)

        await ws.send_bytes(b"echo gapt-ok\n")
        collected = b""
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
            except TimeoutError:
                continue
            if msg.type.name == "BINARY":
                collected += msg.data
                if b"gapt-ok" in collected:
                    break

        assert b"gapt-ok" in collected, f"shell never echoed; saw: {collected!r}"

    await daemon.post(f"/pty/{session_id}/close", headers=_auth())


@pytest.mark.asyncio
async def test_resize_message_accepted(daemon: TestClient) -> None:
    open_resp = await daemon.post(
        "/open_pty",
        json={"shell": "/bin/sh"},
        headers=_auth(),
    )
    session_id = (await open_resp.json())["id"]

    async with daemon.ws_connect(f"/pty/{session_id}", headers=_auth()) as ws:
        await ws.send_str(json.dumps({"type": "resize", "rows": 50, "cols": 132}))
        await asyncio.sleep(0.1)  # let the ioctl land

    # The session record reflects the new size.
    from gapt_runtime.handlers_pty import PTY_MANAGER_KEY  # noqa: PLC0415

    mgr = daemon.app[PTY_MANAGER_KEY]  # type: ignore[index]
    session = mgr.get(session_id)
    assert session is not None
    assert session.rows == 50
    assert session.cols == 132

    await daemon.post(f"/pty/{session_id}/close", headers=_auth())


@pytest.mark.asyncio
async def test_ws_to_missing_pty_returns_404(daemon: TestClient) -> None:
    resp = await daemon.get("/pty/01KSXXXXXXXXXXXXXXXXXXXXXX", headers=_auth())
    assert resp.status == 404


@pytest.mark.asyncio
async def test_close_pty_is_idempotent(daemon: TestClient) -> None:
    open_resp = await daemon.post("/open_pty", json={"shell": "/bin/sh"}, headers=_auth())
    session_id = (await open_resp.json())["id"]
    first = await daemon.post(f"/pty/{session_id}/close", headers=_auth())
    assert first.status == 200
    second = await daemon.post(f"/pty/{session_id}/close", headers=_auth())
    assert second.status == 200
