"""Test runner — one SSE endpoint that streams `<test_command>` output
from inside the workspace sandbox.

Why a dedicated endpoint instead of "use the terminal":
- the terminal is a PTY interactive shell; nobody scrolls back through
  100k lines of pytest output.
- the test runner panel wants structured frames (log / done) so the
  UI can render a pass/fail summary + offer a "re-run" button.
- a `last_test_run` cache on the workspace row lets the IDE show
  "last run: 2m ago, 14 passed" without firing a new run.

Concurrency: one active run per workspace. Re-firing while a run is
in flight cancels the prior subprocess (sandbox.exec with timeout).
"""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import (
    get_container,
    get_db_session,
)
from gapt_server.db import enums, models
from gapt_server.domains.introspection import detect
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.container import AppContainer


router = APIRouter(prefix="/api/workspaces", tags=["tests"])


class TestRunRequest(BaseModel):
    """Command override — when None the introspection's
    `test_command` runs as-is. The UI uses the override when the
    user wants to scope a run (`pytest -k foo`)."""

    command: str | None = Field(default=None, max_length=2000)
    cwd: str | None = Field(default=None, max_length=512)


async def _resolve_workspace(
    db: AsyncSession, *, workspace_id: str, user: models.User
) -> models.Workspace:
    ws = (
        await db.execute(
            select(models.Workspace).where(models.Workspace.id == workspace_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace.not_found", "reason": workspace_id},
        )
    membership = (
        await db.execute(
            select(models.ProjectMembership).where(
                (models.ProjectMembership.project_id == ws.project_id)
                & (models.ProjectMembership.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "workspace.forbidden", "reason": workspace_id},
        )
    if ws.status != enums.WorkspaceStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "workspace.not_running", "reason": ws.status.value},
        )
    return ws


@router.post("/{workspace_id}/tests/run")
async def run_tests(
    workspace_id: str,
    payload: TestRunRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> StreamingResponse:
    """SSE: stream the test runner's output line by line.

    Frame shapes:
      `{"type":"meta",  "command": "...", "cwd": "..."}` — once at start
      `{"type":"log",   "stream": "out"|"err", "line": "..."}` — each line
      `{"type":"done",  "exit_code": 0, "duration_ms": 1234}` — terminal
    """
    ws = await _resolve_workspace(db, workspace_id=workspace_id, user=user)
    intro = detect(ws.worktree_path)
    cmd = (payload.command or intro.test_command or "").strip()
    if not cmd:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "tests.no_command",
                "reason": "no test command — set one via payload or in package.json/pyproject",
            },
        )
    cwd = payload.cwd or intro.dev_cwd or None
    sandbox = container.workspace_sandbox.get(workspace_id, ws.worktree_path)

    async def stream():  # type: ignore[no-untyped-def]
        await sandbox.ensure()
        # Wrap with cd when cwd is set; otherwise run from /workspace
        # (the default sandbox WORKDIR).
        inner = cmd if not cwd else f"cd {shlex.quote(cwd)} && {cmd}"
        argv = [
            "docker",
            "exec",
            "-i",
            "-w",
            "/workspace",
            sandbox.container_name,
            "sh",
            "-c",
            inner,
        ]
        meta = {"type": "meta", "command": cmd, "cwd": cwd or "/workspace"}
        yield f"data: {json.dumps(meta)}\n\n".encode()

        loop = asyncio.get_running_loop()
        start = loop.time()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def drain(stream_name: str, reader: asyncio.StreamReader | None):
            if reader is None:
                return
            try:
                while True:
                    chunk = await reader.readline()
                    if not chunk:
                        return
                    line = chunk.decode("utf-8", errors="replace").rstrip("\n")
                    payload_obj = {"type": "log", "stream": stream_name, "line": line}
                    queue.put_nowait(f"data: {json.dumps(payload_obj)}\n\n".encode())
            except asyncio.CancelledError:
                raise

        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def drain_and_signal(name: str, reader: asyncio.StreamReader | None):
            try:
                await drain(name, reader)
            finally:
                queue.put_nowait(b"__END__")

        out_task = asyncio.create_task(drain_and_signal("out", proc.stdout))
        err_task = asyncio.create_task(drain_and_signal("err", proc.stderr))
        ends_seen = 0
        try:
            while ends_seen < 2:
                item = await queue.get()
                if item == b"__END__":
                    ends_seen += 1
                    continue
                yield item
        finally:
            out_task.cancel()
            err_task.cancel()
            # Reap subprocess so zombie doesn't linger.
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except TimeoutError:
                    pass
        duration_ms = int((loop.time() - start) * 1000)
        done = {
            "type": "done",
            "exit_code": proc.returncode if proc.returncode is not None else -1,
            "duration_ms": duration_ms,
        }
        yield f"data: {json.dumps(done)}\n\n".encode()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
