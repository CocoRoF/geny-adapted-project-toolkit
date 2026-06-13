"""Workspace terminal — WebSocket PTY + log file tail (SSE).

- `WS  /_gapt/api/workspaces/{wid}/terminal` — bidirectional PTY shell
- `GET /_gapt/api/workspaces/{wid}/file-tail?path=...&since_byte=N` — SSE
  tail of a file inside the worktree

WebSocket protocol (JSON frames, both directions):
```
client → server:
  {"type": "input",   "data": "<utf-8 string>"}
  {"type": "resize",  "rows": 24, "cols": 80}
  {"type": "ping"}
server → client:
  {"type": "output",  "data": "<utf-8 string (replace-decoded)>"}
  {"type": "exit",    "code": 0}
  {"type": "error",   "code": "...", "reason": "..."}
  {"type": "pong"}
```
Bytes are utf-8 decoded with `errors="replace"` so terminal output
that happens to land on a multi-byte boundary doesn't crash the
JSON serialiser. xterm.js renders the `�` replacement char as
a faint box — acceptable noise vs. losing chunks.

Auth: session cookie (same `gapt_session` as the REST endpoints).
WebSockets accept cookies automatically when the SPA is same-origin,
which is the case for our Vite proxy + Cloudflare tunnel deployment.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from gapt_server.container import get_db_session
from gapt_server.db import enums, models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.terminal import PtyClosed, PtyHandle
from gapt_server.domains.workspace_sandbox import (
    WorkspaceSandboxError,
    WorkspaceSandboxUnavailable,
)
from gapt_server.routers.auth import get_current_user, get_session_store

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.settings import Settings


router = APIRouter(prefix="/_gapt/api/workspaces", tags=["terminal"])


# Shell choice: login bash. The container image ships bash by default.
def _default_shell() -> list[str]:
    return ["/bin/bash", "-l"]


async def _workspace_or_404(
    db: AsyncSession, user: AdminPrincipal, workspace_id: str
) -> models.Workspace:
    # Single-admin model: any authenticated request can touch any
    # workspace. We still bounce non-running rows with 409 so the
    # caller doesn't get a confusing failure deep in the PTY layer.
    _ = user
    row = (
        await db.execute(select(models.Workspace).where(models.Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace.not_found", "reason": workspace_id},
        )
    if row.status != enums.WorkspaceStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "workspace.not_running",
                "reason": f"workspace {workspace_id} is {row.status.value}",
            },
        )
    return row


@router.websocket("/{workspace_id}/terminal")
async def terminal_ws(
    websocket: WebSocket,
    workspace_id: str,
    rows: int = Query(default=24, ge=1, le=500),
    cols: int = Query(default=80, ge=1, le=500),
) -> None:
    """Bidirectional shell over WebSocket. One PTY per connection."""
    # Resolve auth + workspace BEFORE accepting the socket so a bad
    # request gets a clean 4xx during the HTTP upgrade. WebSocket
    # close-codes >= 4000 are app-defined (per the WS RFC) so the
    # client can distinguish auth vs server errors.
    settings: Settings = websocket.app.state.settings
    container = websocket.app.state.container
    if settings.auth_enabled:
        store = get_session_store()
        cookie = websocket.cookies.get(settings.session_cookie_name)
        if not cookie:
            await websocket.close(code=4401, reason="auth.session.missing")
            return
        session = await store.get(cookie)
        if session is None:
            await websocket.close(code=4401, reason="auth.session.expired")
            return
        principal = AdminPrincipal(id=session.user_id, display_name=session.user_id)
    else:
        principal = AdminPrincipal(id=settings.admin_id, display_name=settings.admin_id)

    if container.session_factory is None:
        await websocket.close(code=1011, reason="db not configured")
        return

    async with container.session_factory() as db:
        try:
            workspace = await _workspace_or_404(db, principal, workspace_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error"}
            code = 4404 if exc.status_code == 404 else 4403 if exc.status_code == 403 else 4409
            await websocket.close(code=code, reason=detail.get("code", "error"))
            return

    await websocket.accept()

    sandbox = container.workspace_sandbox.get(workspace_id, workspace.worktree_path)
    cmd = _default_shell()
    try:
        handle = await sandbox.spawn_pty(cmd=cmd, rows=rows, cols=cols)
    except WorkspaceSandboxUnavailable as exc:
        await websocket.send_json(
            {
                "type": "error",
                "code": exc.code,
                "reason": (
                    f"{exc} — workspace terminals require Docker. "
                    "Install Docker and restart the GAPT server."
                ),
            }
        )
        await websocket.close(code=1011, reason="docker unavailable")
        return
    except WorkspaceSandboxError as exc:
        await websocket.send_json({"type": "error", "code": exc.code, "reason": str(exc)})
        await websocket.close(code=1011, reason="sandbox spawn failed")
        return

    await _drive_socket(websocket, handle)


async def _drive_socket(websocket: WebSocket, handle: PtyHandle) -> None:
    """Two-way bridge: stdin frames → PTY write, PTY output → output
    frames. Tasks exit when either side closes; cleanup is centralised
    in the `finally` so a half-open connection still releases the fd."""

    async def reader() -> None:
        try:
            async for chunk in handle.aiter_output():
                if not chunk:
                    continue
                await websocket.send_json(
                    {"type": "output", "data": chunk.decode("utf-8", errors="replace")}
                )
        except WebSocketDisconnect:
            pass
        finally:
            # Drain the exit code if the child finished and notify the
            # client. Best-effort: socket may already be gone.
            with _suppress():
                exit_code = handle.proc.returncode
                if exit_code is None:
                    exit_code = await handle.wait_exit()
                await websocket.send_json({"type": "exit", "code": exit_code})

    async def writer() -> None:
        try:
            while True:
                frame = await websocket.receive_json()
                ftype = str(frame.get("type", ""))
                if ftype == "input":
                    data = frame.get("data", "")
                    if isinstance(data, str) and data:
                        await handle.write(data.encode("utf-8"))
                elif ftype == "resize":
                    try:
                        r = int(frame.get("rows", handle.rows))
                        c = int(frame.get("cols", handle.cols))
                    except (TypeError, ValueError):
                        continue
                    if 1 <= r <= 500 and 1 <= c <= 500:
                        handle.resize(r, c)
                elif ftype == "ping":
                    await websocket.send_json({"type": "pong"})
                # Unknown frames silently dropped — forward-compat.
        except WebSocketDisconnect:
            pass
        except PtyClosed:
            pass

    reader_task = asyncio.create_task(reader(), name="pty-reader")
    writer_task = asyncio.create_task(writer(), name="pty-writer")
    try:
        done, pending = await asyncio.wait(
            {reader_task, writer_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with _suppress():
                await task
    finally:
        await handle.close()
        with _suppress():
            await websocket.close()


# ────────────────────────────────────────────────── file-tail SSE ──


_TAIL_CHUNK = 8192
_TAIL_POLL_S = 0.4


@router.get("/{workspace_id}/file-tail")
async def file_tail(
    workspace_id: str,
    request: Request,
    path: str = Query(..., min_length=1, max_length=4096),
    since_byte: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """SSE tail of `<worktree>/<path>` — emits one `data:` frame per
    new chunk. Polling-based (no inotify) so it works through every
    sandbox backend; the 400 ms cadence is invisible at human eye
    speeds. Each frame's `id:` is the byte offset after the chunk,
    so the SPA can reconnect with `?since_byte=...` and skip what it
    already saw."""
    workspace = await _workspace_or_404(db, user, workspace_id)

    # Path traversal guard — same shape as the files API.
    relative = path.lstrip("/")
    if any(seg in {"..", ""} for seg in relative.split("/")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "tail.path.invalid", "reason": path},
        )
    abs_path = os.path.join(workspace.worktree_path, relative)
    # `..`-rejection alone is not enough: this reads the file HOST-side,
    # and the worktree is a bind mount the semi-trusted agent can write.
    # A symlink it plants (e.g. .gapt/services/x.log → /etc/passwd or a
    # host ssh key) would otherwise be followed off-tree. Resolve the
    # real path and require it to stay within the worktree. We resolve
    # the PARENT (the log file may not exist yet — tail -F waits for it)
    # and re-check the final realpath on every open below.
    worktree_real = os.path.realpath(workspace.worktree_path)

    def _within_worktree(p: str) -> bool:
        real = os.path.realpath(p)
        return real == worktree_real or real.startswith(worktree_real + os.sep)

    if not _within_worktree(os.path.dirname(abs_path) or abs_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "tail.path.escapes_worktree", "reason": path},
        )

    # Native EventSource can't change its URL on auto-reconnect, but it
    # DOES resend the last `id:` it saw via the `Last-Event-Id` header.
    # Honour it (over the initial `since_byte` query) so a transient
    # drop resumes from the byte offset already delivered instead of
    # re-streaming the whole file — the "log doubled after a blip" bug.
    resume_offset = since_byte
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        try:
            resume_offset = max(resume_offset, int(last_event_id))
        except ValueError:
            pass

    async def stream():  # type: ignore[no-untyped-def]
        offset = resume_offset
        # First touch: if the file doesn't exist yet, wait for it.
        # `tail -F` semantics.
        idle_loops = 0
        while True:
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                size = -1
            if size < 0:
                # File missing — wait + keepalive.
                await asyncio.sleep(_TAIL_POLL_S)
                idle_loops += 1
                if idle_loops % 25 == 0:
                    yield b": waiting-for-file\n\n"
                continue
            if size < offset:
                # File was truncated — restart from 0 so we don't miss
                # the new tail.
                offset = 0
            if size > offset:
                # Re-check on every open: a symlink could be swapped in
                # after the initial parent check (TOCTOU). If the file
                # now resolves outside the worktree, stop streaming.
                if not _within_worktree(abs_path):
                    yield b": rejected-symlink-escape\n\n"
                    return
                with open(abs_path, "rb") as fh:
                    fh.seek(offset)
                    while True:
                        chunk = fh.read(_TAIL_CHUNK)
                        if not chunk:
                            break
                        offset += len(chunk)
                        text = chunk.decode("utf-8", errors="replace")
                        payload = json.dumps({"text": text}, ensure_ascii=False)
                        yield f"id: {offset}\ndata: {payload}\n\n".encode()
                idle_loops = 0
                continue
            await asyncio.sleep(_TAIL_POLL_S)
            idle_loops += 1
            if idle_loops % 25 == 0:
                yield b": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


class _suppress:
    """Tiny `with` block — eats every exception for cleanup paths."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return True
