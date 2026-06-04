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

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — pydantic introspection
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.agent.hooks import ChatModeRef, build_hook_runner
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
from gapt_server.agent.streaming import SessionEvent, SessionEventKind
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
from gapt_server.domains.auth import AdminPrincipal  # noqa: TC001 — Depends inspects at runtime
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
    # Phase G.4 — per-session manifest overrides. All optional;
    # missing fields fall through to the global Settings → Pipeline
    # overrides prefs, which fall through to the manifest's bundled
    # defaults. Applied at session-create time only — flipping these
    # mid-conversation requires starting a new session.
    model: str | None = Field(default=None, max_length=120)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    max_iterations: int | None = Field(default=None, ge=1, le=200)
    cost_budget_usd: float | None = Field(default=None, ge=0.0, le=1_000.0)
    timeout_s: int | None = Field(default=None, ge=1, le=3_600)
    # Phase L.4 — Anthropic extended-thinking knobs. Budget in tokens
    # (the API rejects sub-1024 or absurd >200_000); `thinking_enabled`
    # null means "use whatever the manifest / prior overrides decided".
    thinking_enabled: bool | None = None
    thinking_budget_tokens: int | None = Field(default=None, ge=0, le=200_000)


class SessionResponse(BaseModel):
    id: str
    project_id: str
    workspace_id: str
    env_manifest_id: str
    status: enums.AgentSessionStatus
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    # Phase K.2 — Anthropic cache token counts. Default 0 so the
    # response shape stays compatible with clients that haven't
    # learned about them yet.
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    last_active_at: datetime
    created_at: datetime
    # Phase J.1 — list-view enrichments. `turn_count` is the count
    # of `user_message` events for the session; `first_user_message`
    # is the truncated text of the very first prompt — together they
    # let SessionsHistory cards show "what was this session about" at
    # a glance without fetching the full transcript per row.
    turn_count: int = 0
    first_user_message: str | None = None

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
            cache_write_tokens=row.cache_write_tokens,
            cache_read_tokens=row.cache_read_tokens,
            last_active_at=row.last_active_at,
            created_at=row.created_at,
        )


class InvokeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=64_000)
    # Phase D.1 — Plan/Act mode. When "plan", the per-session policy
    # hook short-circuits every mutating tool (gapt_edit/gapt_git/
    # gapt_pr) to a block. Defaults to "act" so legacy clients keep
    # the prior behaviour.
    mode: Literal["plan", "act"] = "act"
    # Phase L follow-up — per-invoke model + thinking override.
    # `state.model` / `state.thinking_*` are read by the api stage's
    # `resolve_model_config` at call time, so mutating them between
    # invokes is the executor-sanctioned way to change behavior mid-
    # conversation without re-instantiating the pipeline. Pre-fix,
    # the chat panel locked these pills the moment a session existed
    # and the operator had to start a new session to try opus on a
    # single follow-up — explicitly listed as a wart by the user.
    model: str | None = Field(default=None, max_length=120)
    thinking_enabled: bool | None = None
    thinking_budget_tokens: int | None = Field(default=None, ge=0, le=200_000)
    # Phase M.2 — revert sentinels. Listing a name in `clear` resets
    # that field back to the session's manifest baseline (captured at
    # first override). Recognised names: `"model"`,
    # `"thinking_enabled"`, `"thinking_budget_tokens"`, and the
    # convenience alias `"thinking"` (which clears both thinking_*
    # fields). The chat UI's pill reset button posts the appropriate
    # name(s) — operators can finally back out of an "I tried opus on
    # one turn" without starting a fresh session.
    clear: list[str] | None = Field(default=None, max_length=8)


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


def _extract_api_model(env_service: Any, env_manifest_id: str) -> str | None:
    """Resolve the manifest's api stage `model` string.

    The Pipeline object itself doesn't carry the manifest (only the
    instantiated stage objects), so we re-resolve via `env_service`
    which knows the on-disk + bundled manifest layout. Returns None
    when the manifest is missing or has no api-stage model — the
    fallback-pricing path then degrades to "no fallback" (the
    accumulator keeps whatever the executor said).

    The model string we hand the pricing layer can be an alias
    (`sonnet`) or a canonical id (`claude-sonnet-4-6`); the resolver
    in `agent/pricing.py` handles both.
    """
    if env_service is None or not env_manifest_id:
        return None
    try:
        resolution = env_service.resolve(env_manifest_id)
    except Exception:
        return None
    manifest = getattr(resolution, "manifest", None)
    if manifest is None:
        return None
    stages = getattr(manifest, "stages", None)
    if stages is None and isinstance(manifest, dict):
        stages = manifest.get("stages")
    if not stages:
        return None
    for stage in stages:
        name = stage.get("name") if isinstance(stage, dict) else getattr(stage, "name", None)
        if name != "api":
            continue
        cfg = stage.get("config") if isinstance(stage, dict) else getattr(stage, "config", None)
        if isinstance(cfg, dict):
            model = cfg.get("model")
            if isinstance(model, str) and model.strip():
                return model.strip()
    return None


def _build_runtime_from_handle(  # noqa: PLR0915 — inline closure builders (cost callback / persister / hooks); refactor lives outside M.4 scope.
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
    # Phase D.1 — shared Plan/Act mode reference. Plumbed into the
    # policy hook AND the runtime so `invoke(mode=...)` can mutate it
    # in place. Default is "act" — Plan mode is opt-in per invoke.
    mode_ref = ChatModeRef(mode="act")
    # Phase M.2 — pin the manifest's bundled api stage model as the
    # baseline for revert. Without this, "clear" reverts to whatever
    # admin prefs locked into `_config.model.model` at pipeline build
    # time (e.g. opus) rather than the bundled "sonnet" the chat
    # panel's "inherit (uses sonnet)" label promises.
    bundled_model = None
    try:
        bundled_model = container.env_service.bundled_api_model(handle.env_manifest_id)
    except Exception:
        bundled_model = None
    runtime = SessionRuntime(
        session_id=handle.session_id,
        project_id=handle.project_id,
        workspace_id=handle.workspace_id,
        user_id=handle.user_id,
        pipeline=handle.pipeline,
        accumulator=placeholder_accumulator,
        sandbox=sandbox,
        mode_ref=mode_ref,
        max_state_messages=container.settings.session_max_messages_in_state,
        _baseline_model=bundled_model,
    )

    # Phase D.3 — persist every published event to `session_events`
    # so a backend restart doesn't blank the chat. The persister
    # runs outside the bus lock; failures are swallowed (we keep the
    # live stream going). Skipped when there's no session factory
    # (test paths that construct runtimes without a DB).
    sf = container.session_factory
    if sf is not None:
        async def _persist_event(event: Any) -> None:
            async with sf() as bg_db:
                bg_db.add(
                    models.SessionEvent(
                        session_id=handle.session_id,
                        seq=event.seq,
                        kind=event.kind.value
                        if hasattr(event.kind, "value")
                        else str(event.kind),
                        data=event.data or {},
                        ts=event.ts,
                    )
                )
                await bg_db.commit()

        runtime.bus.persister = _persist_event

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
                    # Phase K.2 — cache tokens are unconditionally
                    # written so the dashboard shows their growth in
                    # real time. Idempotent (set to acc snapshot).
                    row.cache_write_tokens = acc.cache_write_tokens
                    row.cache_read_tokens = acc.cache_read_tokens
                    await bg_db.commit()

    hook_runner, accumulator = build_hook_runner(
        engine=policy_engine,
        audit_sink=audit_sink,
        actor_id=user.id,
        project_id=handle.project_id,
        workspace_id=handle.workspace_id,
        session_id=handle.session_id,
        on_cost_update=_on_cost_update,
        mode_ref=mode_ref,
    )
    runtime.accumulator = accumulator
    # Phase I.1 — same callback the POST_TOOL_USE hook gets, so the
    # `token.tracked` path in `_drive_pipeline` can land the cost in
    # the DB too. Both paths are delta-detection idempotent (`_last`
    # cache) so double-firing on tool-using turns is a no-op.
    runtime.cost_callback = _on_cost_update
    # Phase I.3 — resolve the manifest's api-stage model so the
    # fallback-pricing path knows which prices to apply when the
    # upstream token stage emits cost_usd=0 (model-alias miss).
    runtime.model_name = _extract_api_model(
        container.env_service, handle.env_manifest_id
    )
    handle.pipeline.attach_runtime(hook_runner=hook_runner)
    return runtime


@dataclass
class SessionAccess:
    """Phase M.4 — bundle the 8 Depends every session route needs into
    one parameter. Pre-M.4 each handler signature carried a vertical
    column of `Depends(get_*)` lines; the bundle cuts that to a single
    `access: SessionAccess = Depends(get_session_access)`.

    Holds the live `db` session + the request principal alongside the
    container-scoped singletons. Constructed by FastAPI via the
    `get_session_access` sub-Depends below — each field's source dep
    remains independently overridable in tests (via `dependency_overrides`).
    """

    registry: SessionRegistry
    db: AsyncSession
    manager: ProjectAwareSessionManager
    policy_engine: PolicyEngine
    audit_sink: AuditSink
    container: AppContainer
    vault: SecretVault
    user: AdminPrincipal


def get_session_access(
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
    vault: SecretVault = Depends(get_vault),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> SessionAccess:
    return SessionAccess(
        registry=registry,
        db=db,
        manager=manager,
        policy_engine=policy_engine,
        audit_sink=audit_sink,
        container=container,
        vault=vault,
        user=user,
    )


async def _runtime_or_rehydrate(
    session_id: str,
    *,
    access: SessionAccess,
) -> SessionRuntime:
    """Fetch the runtime from the registry; if missing, rehydrate
    from the DB row + re-register. The runtime cache is in-process —
    a server restart empties it, but the user's chat panel still
    holds an `active` session id (so the panel correctly auto-resumes
    instead of forcing the user to start a new session every time the
    backend restarts)."""
    registry = access.registry
    db = access.db
    manager = access.manager
    user = access.user
    container = access.container
    policy_engine = access.policy_engine
    audit_sink = access.audit_sink
    vault = access.vault

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
    # Phase D.3 — seed the in-memory bus seq from the persisted max
    # so events published *after* rehydration don't collide with
    # rows already in `session_events`. The chat client then asks
    # `/messages?since=N` which falls through to the DB for the
    # pre-restart history.
    max_seq_row = (
        await db.execute(
            sa.select(sa.func.max(models.SessionEvent.seq)).where(
                models.SessionEvent.session_id == session_id
            )
        )
    ).scalar()
    if max_seq_row is not None:
        runtime.bus.seed_seq(int(max_seq_row))

    # Phase L.1 — reconstruct prior conversation messages from
    # session_events so the rehydrated runtime carries the same
    # `state.messages` the pre-restart pipeline had. Without this
    # step, a server restart turns the chat into amnesia — the agent
    # sees only the new turn even though the user can see the full
    # archive in the UI.
    #
    # Phase M.1 — cap the rehydrate row count via Settings. A long-
    # lived session would otherwise pull thousands of events into
    # memory on every rehydrate. We fetch the latest N by `seq DESC`
    # then reverse to ascending order so `build_transcript`'s turn
    # pairing sees the rows in their natural sequence. The oldest
    # turns are intentionally dropped — the agent loses very-old
    # memory but the recent context the user is actually working with
    # stays intact.
    rehydrate_limit = container.settings.session_max_rehydrate_events
    event_rows_desc = (
        await db.execute(
            sa.select(models.SessionEvent)
            .where(models.SessionEvent.session_id == session_id)
            .order_by(models.SessionEvent.seq.desc())
            .limit(rehydrate_limit)
        )
    ).scalars().all()
    event_rows = list(reversed(event_rows_desc))
    if event_rows:
        from geny_executor.core.state import PipelineState  # noqa: PLC0415

        from gapt_server.agent.transcript import (  # noqa: PLC0415
            build_transcript,
            to_anthropic_messages,
        )

        transcript = build_transcript(
            session_id=session_id,
            events=[
                {
                    "seq": r.seq,
                    "kind": r.kind,
                    "data": r.data or {},
                    "ts": r.ts.isoformat() if r.ts else None,
                }
                for r in event_rows
            ],
        )
        # `session_max_messages_in_state` is an entry count (one user
        # turn + one assistant turn = 2 entries). `to_anthropic_messages`
        # caps in turn-pairs, so we halve. Floor at 1 so a single-turn
        # cap doesn't collapse to zero memory.
        max_turns = max(1, container.settings.session_max_messages_in_state // 2)
        msgs = to_anthropic_messages(transcript, max_turns=max_turns)
        if msgs:
            runtime.conversation_state = PipelineState(
                session_id=session_id,
                messages=msgs,
            )
            logger.info(
                "session.rehydrate.messages_restored",
                session_id=session_id,
                message_count=len(msgs),
                turn_count=len(transcript.turns),
            )
    await registry.register(runtime)
    logger.info(
        "session.rehydrate.registered",
        session_id=session_id,
        project_id=handle.project_id,
        seeded_seq=int(max_seq_row) if max_seq_row else 0,
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
    # Phase G.4 — assemble per-session manifest overrides from the
    # request body. `has_any()` lets us skip the build when the
    # caller didn't ask for anything, so global prefs still win.
    from gapt_server.agent.environment_service import ManifestOverrides  # noqa: PLC0415

    session_overrides: ManifestOverrides | None = None
    if any(
        v is not None
        for v in (
            payload.model,
            payload.max_tokens,
            payload.max_iterations,
            payload.cost_budget_usd,
            payload.timeout_s,
            payload.thinking_enabled,
            payload.thinking_budget_tokens,
        )
    ):
        session_overrides = ManifestOverrides(
            model=payload.model,
            max_tokens=payload.max_tokens,
            max_iterations=payload.max_iterations,
            cost_budget_usd=payload.cost_budget_usd,
            timeout_s=payload.timeout_s,
            thinking_enabled=payload.thinking_enabled,
            thinking_budget_tokens=payload.thinking_budget_tokens,
        )

    try:
        handle = await manager.create_session(
            db,
            user=user,
            workspace_id=payload.workspace_id,
            env_id=payload.env_id,
            vault=vault,
            session_overrides=session_overrides,
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
    include_archived: bool = False,
    workspace_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    manager: ProjectAwareSessionManager = Depends(get_session_manager),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[SessionResponse]:
    del manager  # signature kept for future hooks
    try:
        await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    stmt = select(models.AgentSession).where(
        models.AgentSession.project_id == project_id
    )
    if not include_archived:
        stmt = stmt.where(
            models.AgentSession.status != enums.AgentSessionStatus.ARCHIVED
        )
    # Phase L.3 — workspace_id filter so the ChatPanel's SessionPicker
    # only shows the picker-relevant rows (the operator's workspaces
    # might each have their own session history; mixing them in one
    # picker would confuse the switch action).
    if workspace_id is not None:
        stmt = stmt.where(models.AgentSession.workspace_id == workspace_id)
    # Order by last_active_at DESC so the recently-touched session
    # floats to the top of the picker (Phase L convention: picker is
    # a recency list, not a calendar).
    rows = (
        (
            await db.execute(
                stmt.order_by(models.AgentSession.last_active_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []

    # Phase J.1 — backfill turn_count + first_user_message in two
    # follow-up queries (one count + one ROW_NUMBER-style first row)
    # so the list view doesn't need a separate fetch per card. We
    # only pay for what the page asked for — both queries are bounded
    # by the (session_id IN …) filter.
    session_ids = [r.id for r in rows]
    turn_counts: dict[str, int] = {}
    first_msgs: dict[str, str] = {}

    cnt_rows = (
        await db.execute(
            select(
                models.SessionEvent.session_id,
                sa.func.count().label("c"),
            )
            .where(
                models.SessionEvent.session_id.in_(session_ids),
                models.SessionEvent.kind == "user_message",
            )
            .group_by(models.SessionEvent.session_id)
        )
    ).all()
    for sid, c in cnt_rows:
        turn_counts[sid] = int(c)

    # First user_message per session — DISTINCT ON (PG-specific but
    # we're already PG-locked). One row per session_id with the
    # smallest seq.
    first_rows = (
        await db.execute(
            select(models.SessionEvent.session_id, models.SessionEvent.data)
            .where(
                models.SessionEvent.session_id.in_(session_ids),
                models.SessionEvent.kind == "user_message",
            )
            .distinct(models.SessionEvent.session_id)
            .order_by(
                models.SessionEvent.session_id,
                models.SessionEvent.seq.asc(),
            )
        )
    ).all()
    for sid, data in first_rows:
        if isinstance(data, dict):
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                # Snippet cap so a 5KB prompt doesn't bloat the JSON
                # response. 200 chars is enough to recognise a turn
                # at a glance.
                snippet = text.strip()
                if len(snippet) > 200:
                    snippet = snippet[:200] + "…"
                first_msgs[sid] = snippet

    out: list[SessionResponse] = []
    for r in rows:
        resp = SessionResponse.from_row(r)
        resp.turn_count = turn_counts.get(r.id, 0)
        resp.first_user_message = first_msgs.get(r.id)
        out.append(resp)
    return out


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
    access: SessionAccess = Depends(get_session_access),  # noqa: B008
) -> InvokeResponse:
    runtime = await _runtime_or_rehydrate(session_id, access=access)
    # Re-check membership using the runtime's project_id (in-memory).
    try:
        await fetch_project_for(access.db, actor=access.user, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    # Phase M.2 — per-invoke model + thinking override (and revert).
    # We mutate `pipeline._config.model.*` (NOT `state.*`) because the
    # executor's `_init_state` calls `_config.apply_to_state(state)`
    # on every `run_stream`, which overwrites any state-level edit
    # before the api stage reads it. Pre-M.2 the GAPT code edited
    # `state.model` and looked like it worked, but the executor was
    # silently resetting it on the next turn — operators thought "I
    # switched to opus" while the manifest model kept running.
    #
    # The runtime helper also handles `clear=[...]` revert sentinels,
    # capturing a manifest baseline on the first override request so
    # a subsequent clear can restore it without a fresh session.
    runtime.apply_per_invoke_overrides(
        model=payload.model,
        thinking_enabled=payload.thinking_enabled,
        thinking_budget_tokens=payload.thinking_budget_tokens,
        clear=payload.clear,
    )

    try:
        await runtime.invoke(payload.message, mode=payload.mode)
    except SessionAlreadyInvoking as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "session.already_invoking", "reason": str(exc)},
        ) from exc
    return InvokeResponse(session_id=session_id)


@by_id.get("/{session_id}/stream")
async def stream_session(
    session_id: str,
    access: SessionAccess = Depends(get_session_access),  # noqa: B008
    since: int | None = Query(default=None, ge=0, description="replay events with seq > since"),
) -> StreamingResponse:
    runtime = await _runtime_or_rehydrate(session_id, access=access)
    try:
        await fetch_project_for(access.db, actor=access.user, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    # Phase L follow-up — for a rehydrated session the in-memory ring
    # buffer is empty, so `bus.replay(since)` returns nothing. Match
    # what /messages does: pull the missing prefix from `session_events`
    # so a fresh tab on an existing session shows the full transcript
    # immediately, not a blank pane waiting for new turns.
    effective_since = since or 0
    prefix_events = await _full_replay(
        access.db,
        runtime,
        since=effective_since,
        max_events=access.container.settings.session_max_stream_replay_events,
    )
    return StreamingResponse(
        stream_to_async_iter(
            runtime, replay_since=None, prefix_events=prefix_events
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


async def _full_replay(
    db: AsyncSession,
    runtime: SessionRuntime,
    *,
    since: int,
    max_events: int | None = None,
) -> list[SessionEvent]:
    """Combine the in-memory ring buffer with the durable `session_events`
    table to produce the full ordered event list with `seq > since`.

    The shape mirrors what `/messages` does — bus first if it covers
    everything, otherwise DB-prefix + bus-tail (the bus owns any events
    published after rehydrate). Returns the same `SessionEvent`
    objects the SSE producer would have yielded from the bus, so the
    `.to_sse()` rendering downstream is identical.

    Phase M.1 — when `max_events` is set, the DB prefix is capped to
    the most-recent N rows (DESC + LIMIT, then reversed). The in-memory
    tail is always preserved verbatim — chat clients depend on seeing
    the freshly-published events of the current turn. The cap drops
    the oldest persisted events for a session whose history is older
    than the configured ceiling; the UI's transcript / archive paths
    remain authoritative for the full history.
    """
    in_memory = await runtime.bus.replay(since)
    needs_db_fill = bool(in_memory) and in_memory[0].seq > since + 1
    # `_persisted_seq` is bumped on rehydrate; if it's ahead of `since`
    # and the in-memory buffer is empty, we know everything lives in DB.
    no_memory = not in_memory and (
        runtime.bus._persisted_seq > since
    )
    if not (no_memory or needs_db_fill):
        return list(in_memory)

    upper = in_memory[0].seq - 1 if in_memory else None
    stmt = (
        sa.select(models.SessionEvent)
        .where(
            models.SessionEvent.session_id == runtime.session_id,
            models.SessionEvent.seq > since,
        )
    )
    if upper is not None:
        stmt = stmt.where(models.SessionEvent.seq <= upper)
    if max_events is not None:
        # Newest-first + LIMIT keeps the freshest persisted prefix; we
        # reverse below before merging with the (already ascending)
        # in-memory tail so downstream consumers see a single monotonic
        # seq ordering.
        stmt = stmt.order_by(models.SessionEvent.seq.desc()).limit(max_events)
        rows = list((await db.execute(stmt)).scalars().all())
        rows.reverse()
    else:
        stmt = stmt.order_by(models.SessionEvent.seq.asc())
        rows = list((await db.execute(stmt)).scalars().all())
    db_events = [
        SessionEvent(
            seq=r.seq,
            kind=SessionEventKind(r.kind),
            data=r.data or {},
            ts=r.ts,
        )
        for r in rows
    ]
    return db_events + list(in_memory)


class OverridePatch(BaseModel):
    """Phase M.2 — partial override update applied immediately (no
    LLM call). `clear` lists field names to revert to the manifest
    baseline; set fields override on next invoke. When neither is
    set the request is a no-op (returns the runtime's current snapshot)."""

    model: str | None = Field(default=None, max_length=120)
    thinking_enabled: bool | None = None
    thinking_budget_tokens: int | None = Field(default=None, ge=0, le=200_000)
    clear: list[str] | None = Field(default=None, max_length=8)


class OverrideSnapshot(BaseModel):
    """Current resolved values of the per-session overrides. The chat
    UI reads this after a clear so the pills can sync without
    inferring."""

    model: str | None
    thinking_enabled: bool | None
    thinking_budget_tokens: int | None


@by_id.patch(
    "/{session_id}/overrides",
    response_model=OverrideSnapshot,
)
async def patch_session_overrides(
    session_id: str,
    payload: OverridePatch,
    access: SessionAccess = Depends(get_session_access),  # noqa: B008
) -> OverrideSnapshot:
    """Apply a per-invoke override or revert immediately. Lets the
    chat UI's pill reset button restore the manifest baseline without
    waiting for the next user message — important because pre-M.2 a
    cleared pill silently kept the old override running."""
    runtime = await _runtime_or_rehydrate(session_id, access=access)
    try:
        await fetch_project_for(access.db, actor=access.user, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    runtime.apply_per_invoke_overrides(
        model=payload.model,
        thinking_enabled=payload.thinking_enabled,
        thinking_budget_tokens=payload.thinking_budget_tokens,
        clear=payload.clear,
    )
    cfg = getattr(runtime.pipeline, "_config", None)
    model_cfg = getattr(cfg, "model", None) if cfg is not None else None
    if model_cfg is None:
        return OverrideSnapshot(
            model=None, thinking_enabled=None, thinking_budget_tokens=None
        )
    return OverrideSnapshot(
        model=getattr(model_cfg, "model", None),
        thinking_enabled=getattr(model_cfg, "thinking_enabled", None),
        thinking_budget_tokens=getattr(model_cfg, "thinking_budget_tokens", None),
    )


@by_id.post("/{session_id}/interrupt", response_model=InterruptResponse)
async def interrupt_session(
    session_id: str,
    access: SessionAccess = Depends(get_session_access),  # noqa: B008
) -> InterruptResponse:
    runtime = await _runtime_or_rehydrate(session_id, access=access)
    try:
        await fetch_project_for(access.db, actor=access.user, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    cancelled = await runtime.interrupt()
    return InterruptResponse(session_id=session_id, cancelled=cancelled)


@by_id.get("/{session_id}/messages", response_model=list[MessageReplayEntry])
async def replay_messages(
    session_id: str,
    access: SessionAccess = Depends(get_session_access),  # noqa: B008
    since: int = Query(default=0, ge=0, description="replay events with seq > since"),
) -> list[MessageReplayEntry]:
    runtime = await _runtime_or_rehydrate(session_id, access=access)
    db = access.db
    try:
        await fetch_project_for(db, actor=access.user, project_id=runtime.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    # Phase D.3 — prefer the in-memory ring buffer when it covers
    # the requested range, otherwise fall back to the durable
    # `session_events` table. The in-memory bus is faster + carries
    # the live tail; DB replay handles the "I just restarted the
    # server, give me everything since seq 0" case.
    in_memory = await runtime.bus.replay(since)
    # Detect a gap between `since` and the oldest in-memory event.
    # If the in-memory buffer was wiped (rehydrate) or trimmed past
    # `since + 1`, we pull the missing prefix from DB and concat.
    needs_db_fill = bool(in_memory) and in_memory[0].seq > since + 1
    no_memory = not in_memory and (
        runtime.bus._persisted_seq > since
    )
    if no_memory or needs_db_fill:
        upper = in_memory[0].seq - 1 if in_memory else None
        db_stmt = (
            sa.select(models.SessionEvent)
            .where(
                models.SessionEvent.session_id == session_id,
                models.SessionEvent.seq > since,
            )
            .order_by(models.SessionEvent.seq.asc())
        )
        if upper is not None:
            db_stmt = db_stmt.where(models.SessionEvent.seq <= upper)
        rows = (await db.execute(db_stmt)).scalars().all()
        db_entries = [
            MessageReplayEntry(
                seq=r.seq,
                kind=r.kind,
                data=r.data or {},
                ts=r.ts,
            )
            for r in rows
        ]
        return db_entries + [
            MessageReplayEntry(**e.to_dict()) for e in in_memory
        ]
    return [MessageReplayEntry(**e.to_dict()) for e in in_memory]


@by_id.get("/{session_id}/transcript")
async def export_transcript(
    session_id: str,
    access: SessionAccess = Depends(get_session_access),  # noqa: B008
    format: str = Query(default="json", pattern="^(json|markdown)$"),
) -> Response:
    """Phase I.4 — export the full conversation as JSON or markdown.

    Reads every `session_events` row for the session (DB-only — we
    don't need a live runtime since the persister already wrote
    everything), groups frames into turns via `agent.transcript`, and
    returns the requested format. Markdown is the "vibe-coding
    archive" the operator downloads from the chat panel.
    """
    # Resolve the session to enforce membership + a clean 404. We
    # bypass the rehydrate path because the transcript is read-only —
    # spinning up a runtime just to validate access is wasteful when
    # a single SELECT covers it.
    db = access.db
    session_row = (
        await db.execute(
            sa.select(models.AgentSession).where(
                models.AgentSession.id == session_id
            )
        )
    ).scalar_one_or_none()
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session.not_found", "reason": session_id},
        )
    try:
        await fetch_project_for(db, actor=access.user, project_id=session_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    rows = (
        await db.execute(
            sa.select(models.SessionEvent)
            .where(models.SessionEvent.session_id == session_id)
            .order_by(models.SessionEvent.seq.asc())
        )
    ).scalars().all()
    events: list[dict[str, Any]] = [
        {
            "seq": r.seq,
            "kind": r.kind,
            "data": r.data or {},
            "ts": r.ts.isoformat() if r.ts else None,
        }
        for r in rows
    ]

    from gapt_server.agent.transcript import (  # noqa: PLC0415
        build_transcript,
        render_markdown,
        to_dict,
    )

    transcript = build_transcript(session_id=session_id, events=events)
    if format == "markdown":
        body = render_markdown(transcript)
        # Suggest a filename so browsers save with something sensible
        # rather than `transcript`. The chat panel passes a project /
        # workspace hint via the same headers if it wants prettier
        # naming; this is the safe default.
        filename = f"session-{session_id}-transcript.md"
        return Response(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    # Default: JSON. We rely on FastAPI's JSON encoder via Response so
    # the dataclass dict serialises cleanly with the right content-type.
    import json as _json  # noqa: PLC0415

    return Response(
        content=_json.dumps(to_dict(transcript), ensure_ascii=False),
        media_type="application/json",
    )


@by_id.post("/{session_id}/archive", response_model=SessionResponse)
async def archive_session(
    session_id: str,
    access: SessionAccess = Depends(get_session_access),  # noqa: B008
) -> SessionResponse:
    db = access.db
    try:
        await access.manager.archive(db, user=access.user, session_id=session_id)
        await db.commit()
    except SessionManagerError as exc:
        await db.rollback()
        raise _http_from_session_error(exc) from exc
    except ProjectError as exc:
        await db.rollback()
        raise http_from_project_error(exc) from exc

    runtime = await access.registry.pop(session_id)
    if runtime is not None:
        await runtime.aclose()

    row = (
        await db.execute(select(models.AgentSession).where(models.AgentSession.id == session_id))
    ).scalar_one()
    return SessionResponse.from_row(row)


@by_id.post("/{session_id}/reactivate", response_model=SessionResponse)
async def reactivate_session(
    session_id: str,
    access: SessionAccess = Depends(get_session_access),  # noqa: B008
) -> SessionResponse:
    """Phase L.2 — flip an archived session back to `active`.

    Idempotent for already-active sessions (just bumps `last_active_at`).
    The actual conversation memory restoration happens lazily on the
    next `/invoke` or `/stream` via `_runtime_or_rehydrate` — that's
    Phase L.1's job, not this endpoint's.
    """
    db = access.db
    try:
        row = await access.manager.reactivate(db, user=access.user, session_id=session_id)
        await db.commit()
    except SessionManagerError as exc:
        await db.rollback()
        raise _http_from_session_error(exc) from exc
    except ProjectError as exc:
        await db.rollback()
        raise http_from_project_error(exc) from exc
    return SessionResponse.from_row(row)
