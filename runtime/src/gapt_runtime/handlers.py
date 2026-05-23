"""Request handlers for the toolkit-agent daemon.

- ``POST /exec``       — run a command, return stdout/stderr/exit_code.
- ``POST /readfile``   — read a file under ``workspace_root``.
- ``POST /writefile``  — write a file under ``workspace_root``.

All paths are resolved against the workspace root and checked for
traversal — anything outside the root is refused. ``/exec`` enforces a
configurable wall-clock timeout.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from aiohttp import web
from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from gapt_runtime.settings import DaemonSettings

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────── DTOs ──


class ExecRequest(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=256)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=60.0, gt=0, le=600)
    stdin_b64: str | None = None  # base64 if non-empty stdin


class ExecResponse(BaseModel):
    exit_code: int
    stdout_b64: str
    stderr_b64: str
    duration_ms: int


class ReadFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    max_bytes: int = Field(default=1_048_576, gt=0, le=64 * 1_048_576)


class ReadFileResponse(BaseModel):
    path: str
    size: int
    content_b64: str


class WriteFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content_b64: str
    create_parents: bool = True
    mode: int = Field(default=0o644, ge=0, le=0o777)


class WriteFileResponse(BaseModel):
    path: str
    size: int


# ────────────────────────────────────────────────── helpers ──


class WorkspaceTraversalError(RuntimeError):
    pass


def _resolve_under_root(root: Path, raw: str) -> Path:
    """Return the canonical path for `raw` if and only if it lives
    inside `root`. Symlinks that escape the root are rejected too."""
    root_resolved = root.resolve(strict=False)
    candidate = (
        (root_resolved / raw).resolve(strict=False)
        if not Path(raw).is_absolute()
        else Path(raw).resolve(strict=False)
    )
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkspaceTraversalError(
            f"path {raw!r} escapes workspace root {root_resolved}"
        ) from exc
    return candidate


def _settings(request: web.Request) -> DaemonSettings:
    from gapt_runtime.daemon import SETTINGS_KEY  # noqa: PLC0415

    settings: DaemonSettings = request.app[SETTINGS_KEY]
    return settings


# ────────────────────────────────────────────────── handlers ──


async def handle_exec(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        req = ExecRequest.model_validate(body)
    except ValidationError as exc:
        raise web.HTTPBadRequest(text=exc.json()) from exc

    settings = _settings(request)
    cwd_path: Path | None = None
    if req.cwd is not None:
        try:
            cwd_path = _resolve_under_root(settings.workspace_root, req.cwd)
        except WorkspaceTraversalError as exc:
            raise web.HTTPForbidden(reason=str(exc)) from exc

    env = dict(os.environ)
    env.update(req.env)
    stdin_bytes = base64.b64decode(req.stdin_b64) if req.stdin_b64 else None

    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *req.argv,
            cwd=str(cwd_path) if cwd_path is not None else None,
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin_bytes else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise web.HTTPBadRequest(reason=f"binary not found: {req.argv[0]!r}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes), timeout=req.timeout_s
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise web.HTTPRequestTimeout(reason="exec timeout") from exc

    duration_ms = int((time.monotonic() - started) * 1000)
    resp = ExecResponse(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout_b64=base64.b64encode(stdout or b"").decode("ascii"),
        stderr_b64=base64.b64encode(stderr or b"").decode("ascii"),
        duration_ms=duration_ms,
    )
    return web.json_response(resp.model_dump())


async def handle_readfile(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        req = ReadFileRequest.model_validate(body)
    except ValidationError as exc:
        raise web.HTTPBadRequest(text=exc.json()) from exc

    settings = _settings(request)
    try:
        path = _resolve_under_root(settings.workspace_root, req.path)
    except WorkspaceTraversalError as exc:
        raise web.HTTPForbidden(reason=str(exc)) from exc

    if not path.exists():
        raise web.HTTPNotFound(reason=f"{req.path!r} not found")
    if not path.is_file():
        raise web.HTTPBadRequest(reason=f"{req.path!r} is not a regular file")

    size = path.stat().st_size
    if size > req.max_bytes:
        raise web.HTTPRequestEntityTooLarge(actual_size=size, max_size=req.max_bytes)
    content = path.read_bytes()
    resp = ReadFileResponse(
        path=req.path,
        size=size,
        content_b64=base64.b64encode(content).decode("ascii"),
    )
    return web.json_response(resp.model_dump())


async def handle_writefile(request: web.Request) -> web.Response:
    body = await request.json()
    try:
        req = WriteFileRequest.model_validate(body)
    except ValidationError as exc:
        raise web.HTTPBadRequest(text=exc.json()) from exc

    settings = _settings(request)
    try:
        path = _resolve_under_root(settings.workspace_root, req.path)
    except WorkspaceTraversalError as exc:
        raise web.HTTPForbidden(reason=str(exc)) from exc

    try:
        content = base64.b64decode(req.content_b64)
    except ValueError as exc:
        raise web.HTTPBadRequest(reason="invalid base64 content") from exc

    if req.create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(req.mode)

    resp = WriteFileResponse(path=req.path, size=len(content))
    return web.json_response(resp.model_dump())
