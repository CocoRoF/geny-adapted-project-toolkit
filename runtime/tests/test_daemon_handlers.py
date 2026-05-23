"""Daemon handlers — JWT gate, /exec, /readfile, /writefile."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import TYPE_CHECKING

import jwt
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from gapt_runtime.auth import AUDIENCE, ISSUER
from gapt_runtime.daemon import create_app
from gapt_runtime.settings import DaemonSettings

JWT_SECRET = "test-daemon-secret-with-32-plus-characters"
SESSION_ID = "01KS900000000000000000SESS"


def _mint_token(secret: str = JWT_SECRET, *, sub: str = SESSION_ID, ttl_s: int = 60) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": sub,
            "iat": now,
            "exp": now + ttl_s,
        },
        secret,
        algorithm="HS256",
    )


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


def _auth(token: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or _mint_token()}"}


# ─────────────────────────────────────────── auth ──


@pytest.mark.asyncio
async def test_health_does_not_require_token(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.get("/health")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_info_requires_valid_token(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    unauthed = await client.get("/info")
    assert unauthed.status == 401

    authed = await client.get("/info", headers=_auth())
    assert authed.status == 200
    body = await authed.json()
    assert body["project_id"] == "01KS900000000000000000PRJX"


@pytest.mark.asyncio
async def test_expired_token_rejected(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    expired = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": SESSION_ID,
            "iat": int(time.time()) - 1_000,
            "exp": int(time.time()) - 100,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    resp = await client.get("/info", headers=_auth(expired))
    assert resp.status == 401
    assert "expired" in resp.reason.lower()


@pytest.mark.asyncio
async def test_wrong_signature_rejected(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    other = _mint_token(secret="not-the-secret-but-still-32-chars-long")
    resp = await client.get("/info", headers=_auth(other))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_session_mismatch_rejected(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    bad = _mint_token(sub="01KS900000000000000WRONG01")
    resp = await client.get("/info", headers=_auth(bad))
    assert resp.status == 401
    assert "session" in resp.reason.lower()


# ───────────────────────────────────────── exec ──


@pytest.mark.asyncio
async def test_exec_echo_round_trip(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.post(
        "/exec",
        json={"argv": ["echo", "hello"]},
        headers=_auth(),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["exit_code"] == 0
    stdout = base64.b64decode(body["stdout_b64"]).decode("utf-8")
    assert "hello" in stdout


@pytest.mark.asyncio
async def test_exec_timeout(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.post(
        "/exec",
        json={"argv": ["sleep", "5"], "timeout_s": 0.5},
        headers=_auth(),
    )
    assert resp.status == 408


@pytest.mark.asyncio
async def test_exec_cwd_outside_workspace_rejected(
    daemon: tuple[TestClient, Path],
) -> None:
    client, _ = daemon
    resp = await client.post(
        "/exec",
        json={"argv": ["echo", "hi"], "cwd": "/etc"},
        headers=_auth(),
    )
    assert resp.status == 403


# ───────────────────────────────────────── files ──


@pytest.mark.asyncio
async def test_writefile_then_readfile(daemon: tuple[TestClient, Path]) -> None:
    client, workspace = daemon
    content = base64.b64encode(b"hello, world").decode("ascii")
    write_resp = await client.post(
        "/writefile",
        json={"path": "subdir/note.txt", "content_b64": content},
        headers=_auth(),
    )
    assert write_resp.status == 200
    assert (workspace / "subdir" / "note.txt").read_bytes() == b"hello, world"

    read_resp = await client.post(
        "/readfile",
        json={"path": "subdir/note.txt"},
        headers=_auth(),
    )
    assert read_resp.status == 200
    body = await read_resp.json()
    assert base64.b64decode(body["content_b64"]) == b"hello, world"
    assert body["size"] == len(b"hello, world")


@pytest.mark.asyncio
async def test_readfile_path_traversal_rejected(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.post(
        "/readfile",
        json={"path": "../../etc/passwd"},
        headers=_auth(),
    )
    assert resp.status == 403


@pytest.mark.asyncio
async def test_readfile_absolute_outside_root_rejected(
    daemon: tuple[TestClient, Path],
) -> None:
    client, _ = daemon
    resp = await client.post(
        "/readfile",
        json={"path": "/etc/passwd"},
        headers=_auth(),
    )
    assert resp.status == 403


@pytest.mark.asyncio
async def test_readfile_missing_returns_404(daemon: tuple[TestClient, Path]) -> None:
    client, _ = daemon
    resp = await client.post(
        "/readfile",
        json={"path": "nope.txt"},
        headers=_auth(),
    )
    assert resp.status == 404


@pytest.mark.asyncio
async def test_writefile_respects_size_cap_on_read(
    daemon: tuple[TestClient, Path],
) -> None:
    client, _workspace = daemon
    huge = base64.b64encode(b"x" * 4_096).decode("ascii")
    await client.post(
        "/writefile",
        json={"path": "big.bin", "content_b64": huge},
        headers=_auth(),
    )

    resp = await client.post(
        "/readfile",
        json={"path": "big.bin", "max_bytes": 1_024},
        headers=_auth(),
    )
    assert resp.status == 413


@pytest.mark.asyncio
async def test_missing_secret_returns_500(tmp_path: Path) -> None:
    # If GAPT_DAEMON_TOKEN is empty, the daemon should refuse to serve
    # rather than silently accept anything.
    settings = DaemonSettings(
        socket_path=Path("/run/agent.sock"),
        jwt_secret="",  # explicit empty — dev safeguard
        project_id="p",
        workspace_id="w",
        session_id="s",
        workspace_root=tmp_path,
    )
    app = create_app(settings)
    server = TestServer(app)
    async with TestClient(server) as client:
        resp = await client.get("/info", headers={"Authorization": "Bearer anything"})
        assert resp.status == 500
        # /health remains accessible — it doesn't go through middleware.
        assert (await client.get("/health")).status == 200


@pytest.mark.asyncio
async def test_create_app_returns_aiohttp_app(tmp_path: Path) -> None:
    settings = DaemonSettings(
        socket_path=Path("/run/agent.sock"),
        jwt_secret=JWT_SECRET,
        project_id=None,
        workspace_id=None,
        session_id=None,
        workspace_root=tmp_path,
    )
    app = create_app(settings)
    assert isinstance(app, web.Application)
