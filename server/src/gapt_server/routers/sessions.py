"""Session API + SSE endpoints — closes M1-E2.

Routes:

- ``POST /api/projects/{pid}/sessions``           — create + bind pipeline
- ``GET  /api/projects/{pid}/sessions``           — list active for project
- ``GET  /api/sessions/{sid}``                    — fetch one
- ``POST /api/sessions/{sid}/invoke``             — kick off a user turn
- ``GET  /api/sessions/{sid}/stream``             — SSE event stream
- ``POST /api/sessions/{sid}/interrupt``          — cancel running invoke
- ``GET  /api/sessions/{sid}/messages?since=N``   — replay buffer
- ``POST /api/sessions/{sid}/archive``            — archive + tear down

Stream contract documented in `agent/streaming.py`. Cost roll-up is
handled by the HookRunner attached at session create time — the bus
gets a `cost` event whenever the accumulator's snapshot changes by
more than the configured debounce window (handled inside the runtime).

Permissions: every endpoint requires `get_current_user` and re-checks
project membership via `ProjectAwareSessionManager._ensure_project_membership`
(implicit on create / archive; explicit on read paths).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic introspection
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.agent.hooks import build_hook_runner
from gapt_server.agent.hooks.cost_hook import CostAccumulator
from gapt_server.agent.session_manager import (
    ProjectAwareSessionManager,
    SessionManagerError,
)
from gapt_server.agent.session_registry import (
    SessionAlreadyInvoking,
    SessionNotFound,
    SessionRegistry,
    SessionRuntime,
    stream_to_async_iter,
)
from gapt_server.agent.streaming import SessionEventKind
from gapt_server.container import (
    AppContainer,
    get_audit_sink,
    get_container,
    get_db_session,
    get_policy_engine,
    get_session_manager,
    get_session_registry,
)
from gapt_server.db import enums, models
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001 — Depends inspects at runtime
from gapt_server.domains.projects.service import ProjectError
from gapt_server.observability.instruments import (
    cost_counter,
    input_tokens_counter,
    output_tokens_counter,
)
from gapt_server.policy.engine import PolicyEngine  # noqa: TC001
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = structlog.get_logger(__name__)


by_project = APIRouter(prefix="/api/projects", tags=["sessions"])
by_id = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ────────────────────────────────────────────────────────── DTOs ──


class CreateSessionRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    env_id: str | None = None


class SessionResponse(BaseModel):
    id: str
    project_id: str
    workspace_id: str
    user_id: str
    env_manifest_id: str
    status: enums.AgentSessionStatus
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    last_active_at: datetime
    created_at: datetime

    @classmethod
    def from_row(cls, row: models.AgentSession) -> SessionResponse:
        return cls(
            id=row.id,
            project_id=row.project_id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            env_manifest_id=row.env_manifest_id,
            status=row.status,
            cost_usd=float(row.cost_usd),
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            last_active_at=row.last_active_at,
            created_at=row.created_at,
        )


class InvokeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=64_000)


class InvokeResponse(BaseModel):
    session_id: str
    status: str = "running"


class InterruptResponse(BaseModel):
    session_id: str
    cancelled: bool


class MessageReplayEntry(BaseModel):
    seq: int
    kind: str
    data: dict[str, Any]
    ts: datetime


# ───────────────────────────────────────────────── error mapping ──


def _http_from_session_error(exc: SessionManagerError) -> HTTPException:
    if exc.code == "workspace.not_found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "reason": str(exc)},
        )
    if exc.code == "session.not_found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "reason": str(exc)},
        )
    if exc.code == "session.pipeline_boot_failed":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "reason": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": exc.code, "reason": str(exc)},
    )


async def _runtime_or_404(registry: SessionRegistry, session_id: str) -> SessionRuntime:
    try:
        return await registry.get(session_id)
    except SessionNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session.not_found", "reason": f"no runtime for {session_id!r}"},
        ) from exc


# ─────────────────────────────────────────────────── endpoints ──


@by_project.post(
    "/{project_id}/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    project_id: str,
    payload: CreateSessionRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> SessionResponse:
    try:
        handle = await manager.create_session(
            db,
            user=user,
            workspace_id=payload.workspace_id,
            env_id=payload.env_id,
        )
        await db.commit()
    except ProjectError as exc:
        await db.rollback()
        raise http_from_project_error(exc) from exc
    except SessionManagerError as exc:
        await db.rollback()
        raise _http_from_session_error(exc) from exc

    # Verify project membership matches the workspace we just bound to.
    if handle.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "session.workspace_project_mismatch",
                "reason": (
                    f"workspace {payload.workspace_id} belongs to project "
                    f"{handle.project_id!r}, not {project_id!r}"
                ),
            },
        )

    # Attach the HookRunner to the pipeline. The cost callback writes
    # straight to the runtime's bus so SSE listeners see cost updates
    # the moment a tool completes (POST_TOOL_USE — natural debounce).
    placeholder_accumulator = CostAccumulator(session_id=handle.session_id)
    runtime = SessionRuntime(
        session_id=handle.session_id,
        project_id=handle.project_id,
        workspace_id=handle.workspace_id,
        user_id=handle.user_id,
        pipeline=handle.pipeline,
        accumulator=placeholder_accumulator,
    )

    # Track deltas so Prometheus counters get the increment, not the
    # running total. Closure-captured mutables avoid the nonlocal dance.
    _last = {"input": 0, "output": 0, "cost": 0.0}
    _reg = container.registry
    _project_label = {"project_id": handle.project_id}

    async def _on_cost_update(acc: CostAccumulator) -> None:
        await runtime.bus.publish(SessionEventKind.COST, acc.snapshot())
        d_in = acc.input_tokens - _last["input"]
        d_out = acc.output_tokens - _last["output"]
        d_cost = acc.cost_usd - _last["cost"]
        if d_in:
            input_tokens_counter(_reg).inc(d_in, _project_label)
            _last["input"] = acc.input_tokens
        if d_out:
            output_tokens_counter(_reg).inc(d_out, _project_label)
            _last["output"] = acc.output_tokens
        if d_cost:
            cost_counter(_reg).inc(d_cost, _project_label)
            _last["cost"] = acc.cost_usd
        # Persist totals back to the agent_sessions row so the cost
        # dashboard reflects in-flight usage. A fresh session is used
        # so we don't deadlock against the outer request's session
        # (which has already committed and may be closing).
        if container.session_factory is not None and (d_in or d_out or d_cost):
            async with container.session_factory() as bg_db:
                row = await bg_db.get(models.AgentSession, handle.session_id)
                if row is not None:
                    row.cost_usd = acc.cost_usd
                    row.input_tokens = acc.input_tokens
                    row.output_tokens = acc.output_tokens
                    await bg_db.commit()

    hook_runner, accumulator = build_hook_runner(
        engine=policy_engine,
        audit_sink=audit_sink,
        actor_id=user.id,
        project_id=handle.project_id,
        workspace_id=handle.workspace_id,
        session_id=handle.session_id,
        on_cost_update=_on_cost_update,
    )
    runtime.accumulator = accumulator
    handle.pipeline.attach_runtime(hook_runner=hook_runner)
    await registry.register(runtime)

    # Re-read the row so the response carries DB-default fields.
    row = (
        await db.execute(
            select(models.AgentSession).where(models.AgentSession.id == handle.session_id)
        )
    ).scalar_one()
    return SessionResponse.from_row(row)


@by_project.get("/{project_id}/sessions", response_model=list[SessionResponse])
async def list_sessions(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> list[SessionResponse]:
    try:
        await manager._ensure_project_membership(db, user_id=user.id, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    rows = (
        (
            await db.execute(
                select(models.AgentSession)
                .where(
                    (models.AgentSession.project_id == project_id)
                    & (models.AgentSession.status != enums.AgentSessionStatus.ARCHIVED)
                )
                .order_by(models.AgentSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [SessionResponse.from_row(r) for r in rows]


@by_id.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> SessionResponse:
    row = (
        await db.execute(select(models.AgentSession).where(models.AgentSession.id == session_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session.not_found", "reason": session_id},
        )
    try:
        await manager._ensure_project_membership(db, user_id=user.id, project_id=row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return SessionResponse.from_row(row)


@by_id.post(
    "/{session_id}/invoke",
    response_model=InvokeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def invoke_session(
    session_id: str,
    payload: InvokeRequest,
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> InvokeResponse:
    runtime = await _runtime_or_404(registry, session_id)
    # Re-check membership using the runtime's project_id (in-memory).
    try:
        await manager._ensure_project_membership(db, user_id=user.id, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    try:
        await runtime.invoke(payload.message)
    except SessionAlreadyInvoking as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "session.already_invoking", "reason": str(exc)},
        ) from exc
    return InvokeResponse(session_id=session_id)


@by_id.get("/{session_id}/stream")
async def stream_session(
    session_id: str,
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
    since: int | None = Query(default=None, ge=0, description="replay events with seq > since"),
) -> StreamingResponse:
    runtime = await _runtime_or_404(registry, session_id)
    try:
        await manager._ensure_project_membership(db, user_id=user.id, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return StreamingResponse(
        stream_to_async_iter(runtime, replay_since=since),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@by_id.post("/{session_id}/interrupt", response_model=InterruptResponse)
async def interrupt_session(
    session_id: str,
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> InterruptResponse:
    runtime = await _runtime_or_404(registry, session_id)
    try:
        await manager._ensure_project_membership(db, user_id=user.id, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    cancelled = await runtime.interrupt()
    return InterruptResponse(session_id=session_id, cancelled=cancelled)


@by_id.get("/{session_id}/messages", response_model=list[MessageReplayEntry])
async def replay_messages(
    session_id: str,
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
    since: int = Query(default=0, ge=0, description="replay events with seq > since"),
) -> list[MessageReplayEntry]:
    runtime = await _runtime_or_404(registry, session_id)
    try:
        await manager._ensure_project_membership(db, user_id=user.id, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    history = await runtime.bus.replay(since)
    return [MessageReplayEntry(**e.to_dict()) for e in history]


@by_id.post("/{session_id}/archive", response_model=SessionResponse)
async def archive_session(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> SessionResponse:
    try:
        await manager.archive(db, user=user, session_id=session_id)
        await db.commit()
    except SessionManagerError as exc:
        await db.rollback()
        raise _http_from_session_error(exc) from exc
    except ProjectError as exc:
        await db.rollback()
        raise http_from_project_error(exc) from exc

    runtime = await registry.pop(session_id)
    if runtime is not None:
        await runtime.aclose()

    row = (
        await db.execute(select(models.AgentSession).where(models.AgentSession.id == session_id))
    ).scalar_one()
    return SessionResponse.from_row(row)
