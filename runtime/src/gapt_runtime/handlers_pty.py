"""PTY HTTP + WebSocket handlers.

- ``POST /open_pty`` — allocate a pty + spawn shell, return ``{id, rows, cols}``.
- ``WS   /pty/{id}``  — xterm.js-friendly bidirectional bytes.
- ``POST /pty/{id}/close`` — kill the shell + close the pty.

WebSocket protocol (matches xterm.js / vscode terminal conventions):

- Server → client: binary frames carrying raw PTY output bytes.
- Client → server, binary frame: raw input to be written to the PTY.
- Client → server, text frame starting with ``{"type":"resize"``: parsed
  as JSON ``{"type": "resize", "rows": int, "cols": int}``. Other JSON
  messages are reserved for future use and ignored.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING

import structlog
from aiohttp import WSMsgType, web
from pydantic import BaseModel, Field, ValidationError

from gapt_runtime.pty_manager import PtyManager, PtySessionNotFound

if TYPE_CHECKING:
    from gapt_runtime.settings import DaemonSettings

logger = structlog.get_logger(__name__)

PTY_MANAGER_KEY: web.AppKey[PtyManager] = web.AppKey("pty_manager", PtyManager)


# ─────────────────────────────────────────────────────── DTOs ──


class OpenPtyRequest(BaseModel):
    shell: str = Field(default="/bin/bash", min_length=1, max_length=4096)
    cwd: str | None = None
    rows: int = Field(default=24, ge=1, le=512)
    cols: int = Field(default=80, ge=1, le=2048)
    env: dict[str, str] = Field(default_factory=dict)


class OpenPtyResponse(BaseModel):
    id: str
    pid: int
    rows: int
    cols: int


# ──────────────────────────────────────────────────── helpers ──


def _settings(request: web.Request) -> DaemonSettings:
    from gapt_runtime.daemon import SETTINGS_KEY  # noqa: PLC0415

    settings: DaemonSettings = request.app[SETTINGS_KEY]
    return settings


def _manager(request: web.Request) -> PtyManager:
    return request.app[PTY_MANAGER_KEY]


# ────────────────────────────────────────────────── handlers ──


async def handle_open_pty(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        req = OpenPtyRequest.model_validate(body)
    except ValidationError as exc:
        raise web.HTTPBadRequest(text=exc.json()) from exc

    settings = _settings(request)
    cwd = req.cwd or str(settings.workspace_root)

    mgr = _manager(request)
    session = await mgr.open(shell=req.shell, cwd=cwd, env=req.env, rows=req.rows, cols=req.cols)
    resp = OpenPtyResponse(id=session.id, pid=session.pid, rows=session.rows, cols=session.cols)
    return web.json_response(resp.model_dump())


async def handle_close_pty(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    mgr = _manager(request)
    await mgr.close(session_id)
    return web.json_response({"closed": session_id})


async def handle_pty_ws(request: web.Request) -> web.WebSocketResponse:
    session_id = request.match_info["session_id"]
    mgr = _manager(request)
    if mgr.get(session_id) is None:
        raise web.HTTPNotFound(reason=f"pty {session_id!r} not found")

    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(request)

    reader_task: asyncio.Task[None] = asyncio.create_task(
        _pump_pty_to_ws(mgr, session_id, ws),
        name=f"pty.read.{session_id}",
    )

    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                try:
                    await mgr.write(session_id, msg.data)
                except PtySessionNotFound:
                    break
            elif msg.type == WSMsgType.TEXT:
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("type") == "resize":
                    try:
                        await mgr.resize(
                            session_id,
                            rows=int(payload["rows"]),
                            cols=int(payload["cols"]),
                        )
                    except (KeyError, ValueError, TypeError):
                        continue
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break
    finally:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, PtySessionNotFound):
            await reader_task
        if not ws.closed:
            await ws.close()
    return ws


async def _pump_pty_to_ws(mgr: PtyManager, session_id: str, ws: web.WebSocketResponse) -> None:
    """Pump bytes from the PTY master to the WebSocket. Exits when the
    PTY closes (read returns b"") or the WS goes away."""
    try:
        while not ws.closed:
            try:
                chunk = await mgr.read(session_id)
            except PtySessionNotFound:
                return
            except OSError:
                return
            if not chunk:
                return
            try:
                await ws.send_bytes(chunk)
            except ConnectionError:
                return
    except asyncio.CancelledError:
        raise
