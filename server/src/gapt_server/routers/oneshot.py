"""Headless one-shot session endpoint — M5 cron / webhook interface seed.

Single endpoint: `POST /_gapt/api/sessions/oneshot` that

  1. Creates an agent session against an existing workspace
  2. Sends one user message
  3. Waits for the assistant turn to finish (DONE / ERROR / timeout)
  4. Aggregates emitted events into a single JSON response
  5. Archives the session

M1 reuses the existing cookie auth — project-scoped API tokens land in
M5 when the cron scheduler ships. The handler keeps the same hook
runner + audit / cost / policy attachment as the interactive endpoint
so behaviour stays consistent.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from gapt_server.agent.hooks import build_hook_runner
from gapt_server.agent.hooks.cost_hook import CostAccumulator
from gapt_server.agent.session_manager import (
    ProjectAwareSessionManager,
    SessionManagerError,
)
from gapt_server.agent.session_registry import (
    SessionAlreadyInvoking,
    SessionRegistry,
    SessionRuntime,
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
from gapt_server.db import models
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001 — runtime Depends
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.projects.service import ProjectError
from gapt_server.observability.instruments import (
    cost_counter,
    input_tokens_counter,
    output_tokens_counter,
)
from gapt_server.policy.engine import PolicyEngine  # noqa: TC001 — runtime Depends
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/_gapt/api/sessions", tags=["sessions"])


_MAX_TIMEOUT_S = 600
_DEFAULT_TIMEOUT_S = 120


class OneshotRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=200)
    env_id: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1, max_length=64_000)
    timeout_s: int = Field(default=_DEFAULT_TIMEOUT_S, ge=1, le=_MAX_TIMEOUT_S)


class OneshotResponse(BaseModel):
    session_id: str
    status: str  # "ok" | "error" | "timeout"
    exec_code: str | None = None
    error_reason: str | None = None
    text: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    cost: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)


@router.post(
    "/oneshot",
    response_model=OneshotResponse,
    status_code=status.HTTP_200_OK,
)
async def oneshot_session(  # noqa: PLR0915 — sequential setup + drain loop reads cleaner inline
    payload: OneshotRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> OneshotResponse:
    # 1) Create the session via the standard manager path. Membership
    #    is enforced inside `create_session`.
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
        code = exc.code
        http_status = (
            status.HTTP_404_NOT_FOUND
            if "not_found" in code
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": code, "reason": str(exc)},
        ) from exc

    # 2) Build the runtime + hook chain — same shape as the
    #    interactive endpoint.
    placeholder = CostAccumulator(session_id=handle.session_id)
    # Bind the workspace sandbox so the agent CLI runs inside
    # `gapt-ws-<wid>` via the patched `_spawn`. Same logic as the
    # interactive path.
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
        accumulator=placeholder,
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
    await registry.register(runtime)

    # 3) Subscribe BEFORE invoke so we don't miss the first frame.
    queue = await runtime.bus.subscribe()
    try:
        try:
            await runtime.invoke(payload.message)
        except SessionAlreadyInvoking as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "session.already_invoking", "reason": str(exc)},
            ) from exc

        text_chunks: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        final_status: str = "ok"
        exec_code: str | None = None
        error_reason: str | None = None
        final_cost: dict[str, Any] = {}

        async def _drain() -> None:
            while True:
                event = await queue.get()
                if event is None:
                    return  # bus closed
                events.append(event.to_dict())
                if event.kind == SessionEventKind.TEXT:
                    chunk = event.data.get("text") or event.data.get("delta")
                    if isinstance(chunk, str):
                        text_chunks.append(chunk)
                elif event.kind == SessionEventKind.TOOL_CALL:
                    tool_calls.append(dict(event.data))
                elif event.kind == SessionEventKind.TOOL_RESULT:
                    tool_results.append(dict(event.data))
                elif event.kind == SessionEventKind.COST:
                    pass  # last one wins, snapshot pulled at the end
                elif event.kind == SessionEventKind.ERROR:
                    nonlocal final_status, exec_code, error_reason
                    final_status = "error"
                    exec_code = event.data.get("exec_code")
                    error_reason = event.data.get("reason")
                    return
                elif event.kind == SessionEventKind.DONE:
                    nonlocal final_cost
                    final_cost = event.data.get("cost", {}) or {}
                    return

        try:
            await asyncio.wait_for(_drain(), timeout=payload.timeout_s)
        except TimeoutError:
            final_status = "timeout"
            exec_code = "exec.session.timeout"
            error_reason = f"oneshot exceeded {payload.timeout_s}s"
            await runtime.interrupt()
            await runtime.wait_done()

    finally:
        await runtime.bus.unsubscribe(queue)
        # Tear the runtime down so the next oneshot starts clean.
        popped = await registry.pop(handle.session_id)
        if popped is not None:
            await popped.aclose()
        # Archive the DB row.
        try:
            await manager.archive(db, user=user, session_id=handle.session_id)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("oneshot.archive_failed", session_id=handle.session_id)

    return OneshotResponse(
        session_id=handle.session_id,
        status=final_status,
        exec_code=exec_code,
        error_reason=error_reason,
        text="".join(text_chunks),
        tool_calls=tool_calls,
        tool_results=tool_results,
        cost=final_cost or accumulator.snapshot(),
        events=events,
    )
