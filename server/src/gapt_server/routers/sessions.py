"""Session API + SSE endpoints — closes M1-E2.

Routes:

- ``POST /_gapt/api/projects/{pid}/sessions``           — create + bind pipeline
- ``GET  /_gapt/api/projects/{pid}/sessions``           — list active for project
- ``GET  /_gapt/api/sessions/{sid}``                    — fetch one
- ``POST /_gapt/api/sessions/{sid}/invoke``             — kick off a user turn
- ``GET  /_gapt/api/sessions/{sid}/stream``             — SSE event stream
- ``POST /_gapt/api/sessions/{sid}/interrupt``          — cancel running invoke
- ``GET  /_gapt/api/sessions/{sid}/messages?since=N``   — replay buffer
- ``POST /_gapt/api/sessions/{sid}/archive``            — archive + tear down

Stream contract documented in `agent/streaming.py`. Cost roll-up is
handled by the HookRunner attached at session create time — the bus
gets a `cost` event whenever the accumulator's snapshot changes by
more than the configured debounce window (handled inside the runtime).

Permissions: every endpoint requires `get_current_user`; project access
is re-checked via `fetch_project_for` so we get a consistent 404 when
the project_id is bogus.
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
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.domains.secrets.vault import SecretVault  # noqa: TC001
from gapt_server.observability.instruments import (
    cost_counter,
    input_tokens_counter,
    output_tokens_counter,
)
from gapt_server.policy.engine import PolicyEngine  # noqa: TC001
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error
from gapt_server.routers.secrets import get_vault

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = structlog.get_logger(__name__)


by_project = APIRouter(prefix="/_gapt/api/projects", tags=["sessions"])
by_id = APIRouter(prefix="/_gapt/api/sessions", tags=["sessions"])


# ────────────────────────────────────────────────────────── DTOs ──


class CreateSessionRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    env_id: str | None = None


class SessionResponse(BaseModel):
    id: str
    project_id: str
    workspace_id: str
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


def _build_runtime_from_handle(
    handle: Any,
    *,
    user: AdminPrincipal,
    container: AppContainer,
    policy_engine: PolicyEngine,
    audit_sink: AuditSink,
) -> SessionRuntime:
    """Build + wire a `SessionRuntime` from a freshly created or
    rehydrated `AgentSessionHandle`. Mirrors the original inline
    setup so rehydrated sessions behave identically (same hook
    runner / cost callback / accumulator). The caller is responsible
    for registering the runtime in the `SessionRegistry`."""
    placeholder_accumulator = CostAccumulator(session_id=handle.session_id)
    # Bind the workspace's docker sandbox so the invoke runner can
    # route the agent CLI through `docker exec`. Empty worktree path
    # means we lack the host bind-source — skip binding rather than
    # ensure the wrong container.
    sandbox = None
    if handle.worktree_path:
        sandbox = container.workspace_sandbox.get(
            handle.workspace_id, handle.worktree_path
        )
    runtime = SessionRuntime(
        session_id=handle.session_id,
        project_id=handle.project_id,
        workspace_id=handle.workspace_id,
        user_id=handle.user_id,
        pipeline=handle.pipeline,
        accumulator=placeholder_accumulator,
        sandbox=sandbox,
    )

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
    return runtime


async def _runtime_or_rehydrate(
    *,
    registry: SessionRegistry,
    session_id: str,
    db: AsyncSession,
    manager: ProjectAwareSessionManager,
    user: AdminPrincipal,
    container: AppContainer,
    policy_engine: PolicyEngine,
    audit_sink: AuditSink,
    vault: SecretVault,
) -> SessionRuntime:
    """Fetch the runtime from the registry; if missing, rehydrate
    from the DB row + re-register. The runtime cache is in-process —
    a server restart empties it, but the user's chat panel still
    holds an `active` session id (so the panel correctly auto-resumes
    instead of forcing the user to start a new session every time the
    backend restarts)."""
    try:
        return await registry.get(session_id)
    except SessionNotFound:
        pass

    try:
        handle = await manager.rehydrate_session(
            db, user=user, session_id=session_id, vault=vault
        )
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    except SessionManagerError as exc:
        if exc.code == "session.not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": exc.code, "reason": str(exc)},
            ) from exc
        raise _http_from_session_error(exc) from exc

    runtime = _build_runtime_from_handle(
        handle,
        user=user,
        container=container,
        policy_engine=policy_engine,
        audit_sink=audit_sink,
    )
    await registry.register(runtime)
    logger.info(
        "session.rehydrate.registered",
        session_id=session_id,
        project_id=handle.project_id,
    )
    return runtime


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
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> SessionResponse:
    try:
        handle = await manager.create_session(
            db,
            user=user,
            workspace_id=payload.workspace_id,
            env_id=payload.env_id,
            vault=vault,
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

    runtime = _build_runtime_from_handle(
        handle,
        user=user,
        container=container,
        policy_engine=policy_engine,
        audit_sink=audit_sink,
    )
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
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[SessionResponse]:
    try:
        await fetch_project_for(db, actor=user, project_id=project_id)
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
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
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
        await fetch_project_for(db, actor=user, project_id=row.project_id)
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
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> InvokeResponse:
    runtime = await _runtime_or_rehydrate(
        registry=registry, session_id=session_id, db=db, manager=manager,
        user=user, container=container, policy_engine=policy_engine,
        audit_sink=audit_sink, vault=vault,
    )
    # Re-check membership using the runtime's project_id (in-memory).
    try:
        await fetch_project_for(db, actor=user, project_id=runtime.project_id)
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
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    since: int | None = Query(default=None, ge=0, description="replay events with seq > since"),
) -> StreamingResponse:
    runtime = await _runtime_or_rehydrate(
        registry=registry, session_id=session_id, db=db, manager=manager,
        user=user, container=container, policy_engine=policy_engine,
        audit_sink=audit_sink, vault=vault,
    )
    try:
        await fetch_project_for(db, actor=user, project_id=runtime.project_id)
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
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> InterruptResponse:
    runtime = await _runtime_or_rehydrate(
        registry=registry, session_id=session_id, db=db, manager=manager,
        user=user, container=container, policy_engine=policy_engine,
        audit_sink=audit_sink, vault=vault,
    )
    try:
        await fetch_project_for(db, actor=user, project_id=runtime.project_id)
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
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    since: int = Query(default=0, ge=0, description="replay events with seq > since"),
) -> list[MessageReplayEntry]:
    runtime = await _runtime_or_rehydrate(
        registry=registry, session_id=session_id, db=db, manager=manager,
        user=user, container=container, policy_engine=policy_engine,
        audit_sink=audit_sink, vault=vault,
    )
    try:
        await fetch_project_for(db, actor=user, project_id=runtime.project_id)
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
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
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
