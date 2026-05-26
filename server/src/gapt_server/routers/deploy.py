"""Deploy + rollback endpoints.

- `POST /api/environments/{env_id}/deploy {version?, two_factor_code?, target_options?}`
- `POST /api/environments/{env_id}/rollback {run_id, to_version, two_factor_code?}`
- `GET  /api/environments/{env_id}/runs/{run_id}/stream` (SSE)

The router instantiates a fresh `DeployOrchestrator` per request,
binding the right `DeployTarget` for the environment's `deploy_target_kind`.
Concurrency: the router holds a per-environment asyncio.Lock so a
second deploy on the same environment blocks until the first lands.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import (
    get_app_settings,
    get_audit_sink,
    get_db_session,
    get_deploy_registry,
    get_notifications,
    get_policy_engine,
)
from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.caddy.admin_api import CaddyAdminClient, CaddyHttpTransport
from gapt_server.domains.caddy.subdomain import SubdomainManager
from gapt_server.domains.deploy import (
    AcceptAnyCodeVerifier,
    DeployOrchestrator,
    DeployStatusKind,
    DeployTarget,
    DeployTargetError,
    LocalComposeTarget,
    OrchestratorError,
    RemoteSshTarget,
    TwoFactorError,
    TwoFactorVerifier,
    WebhookTarget,
)
from gapt_server.domains.deploy.registry import (
    DeployAlreadyRunning,
    DeployRegistry,
    DeployRunHandle,
    DeployRunNotFound,
    make_log_pump,
)
from gapt_server.domains.deploy.stack_manager import StackManager
from gapt_server.domains.sandbox import make_default_client
from gapt_server.domains.notifications import NotificationKind, NotificationService
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.policy.engine import PolicyEngine  # noqa: TC001
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error
from gapt_server.settings import Settings  # noqa: TC001

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/environments", tags=["deploy"])


# Per-environment serialisation. One lock per env_id; instantiated
# lazily. We never delete locks (the set is bounded by the number of
# distinct environments in the database).
_env_locks: dict[str, asyncio.Lock] = {}


def _lock_for(env_id: str) -> asyncio.Lock:
    lock = _env_locks.get(env_id)
    if lock is None:
        lock = asyncio.Lock()
        _env_locks[env_id] = lock
    return lock


# ──────────────────────────────────────────────── DTOs ──


class DeployRequestBody(BaseModel):
    version: str = Field(default="latest", max_length=200)
    two_factor_code: str | None = Field(default=None, max_length=20)
    target_options: dict[str, Any] = Field(default_factory=dict)


class RollbackRequestBody(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    to_version: str = Field(min_length=1, max_length=200)
    two_factor_code: str | None = Field(default=None, max_length=20)
    target_options: dict[str, Any] = Field(default_factory=dict)


class DeployResultResponse(BaseModel):
    run_id: str
    status: str
    exec_code: str | None = None
    log: str = ""
    bound_url: str | None = None


class RollbackResultResponse(BaseModel):
    run_id: str
    status: str
    restored_version: str | None = None
    exec_code: str | None = None
    log: str = ""


# ──────────────────────────────────────────── target factory ──


def _build_subdomain_manager(settings: Settings) -> SubdomainManager | None:
    """Same lazy-construction as `routers/services.py` — when the
    Caddy admin URL + preview domain are configured, hand a wired
    manager to the LocalComposeTarget for post-up routing. Otherwise
    the deploy still works; it just won't auto-expose a URL."""
    if not settings.caddy_admin_url or not settings.caddy_preview_domain:
        return None
    transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
    client = CaddyAdminClient(transport=transport)
    return SubdomainManager(client=client, preview_domain=settings.caddy_preview_domain)


def _build_target(kind: enums.DeployTargetKind, settings: Settings) -> DeployTarget:
    if kind == enums.DeployTargetKind.LOCAL:
        # LocalComposeTarget gets the SubdomainManager when Caddy is
        # wired; that's what makes the prod stack externally
        # reachable after a successful `up -d`.
        return LocalComposeTarget(subdomain_manager=_build_subdomain_manager(settings))
    if kind == enums.DeployTargetKind.REMOTE_SSH:
        return RemoteSshTarget()
    if kind == enums.DeployTargetKind.WEBHOOK:
        return WebhookTarget()
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "deploy.target_unsupported",
            "reason": f"deploy target kind {kind.value!r} is not implemented yet",
        },
    )


# ──────────────────────────────────────────────── endpoints ──


def get_two_factor_verifier() -> TwoFactorVerifier:
    """DI seam — production swaps in a TOTP-backed verifier once
    `users.totp_secret_encrypted` ships. The dev stub accepts any
    non-empty code."""
    return AcceptAnyCodeVerifier()


async def _resolve_env(
    db: AsyncSession,
    *,
    env_id: str,
) -> models.Environment:
    row = (
        await db.execute(select(models.Environment).where(models.Environment.id == env_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "environment.not_found", "reason": env_id},
        )
    return row


async def _record_deploy_run(
    db: AsyncSession,
    *,
    env_row: models.Environment,
    version: str,
    status_value: str,
    bound_url: str | None,
    exec_code: str | None,
    log_tail: str,
    actor_id: str | None,
    trigger_kind: str,
) -> models.DeployRun:
    """Append a `DeployRun` row + (on success) update the env's
    cached `last_run`. Both bits stay consistent because they live
    in the same transaction.

    `bound_url` is read off the `DeployResult` when the LocalCompose
    target finishes its routing step. `log_tail` is whatever the
    target captured up to the per-run limit (~2 KB).
    """
    run = models.DeployRun(
        id=new_ulid(),
        environment_id=env_row.id,
        version=version,
        status=status_value,
        bound_url=bound_url,
        exec_code=exec_code,
        log_tail=log_tail or "",
        finished_at=datetime.now(tz=UTC),
        actor_id=actor_id,
        trigger_kind=trigger_kind,
    )
    db.add(run)
    if status_value == "success":
        env_row.last_run = {
            "run_id": run.id,
            "status": status_value,
            "bound_url": bound_url,
            "version": version,
            "deployed_at": run.finished_at.isoformat() if run.finished_at else None,
            "trigger_kind": trigger_kind,
        }
    await db.flush()
    return run


@router.post(
    "/{env_id}/deploy",
    response_model=DeployResultResponse,
    status_code=status.HTTP_200_OK,
)
async def trigger_deploy(
    env_id: str,
    payload: DeployRequestBody,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    two_factor: TwoFactorVerifier = Depends(get_two_factor_verifier),  # noqa: B008
    notifications: NotificationService = Depends(get_notifications),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> DeployResultResponse:
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    target = _build_target(env_row.deploy_target_kind, settings)
    orchestrator = DeployOrchestrator(
        policy_engine=policy_engine,
        target=target,
        audit_sink=audit_sink,
        two_factor=two_factor,
    )

    compose_cfg = (
        env_row.deploy_target_config if isinstance(env_row.deploy_target_config, dict) else {}
    )
    compose_path = compose_cfg.get("compose_path", "docker-compose.yml")
    raw_paths = compose_cfg.get("compose_paths") or []
    compose_paths: list[str] = [p for p in raw_paths if isinstance(p, str)]
    # Merge env-level options with caller overrides (caller wins, eg
    # one-off SSH host). secret_refs come from the env row.
    target_options: dict[str, Any] = {
        **(env_row.deploy_target_config or {}),
        **payload.target_options,
    }

    async with _lock_for(env_id):
        try:
            result = await orchestrator.deploy(
                actor_id=user.id,
                project_id=env_row.project_id,
                environment_id=env_row.id,
                environment_name=env_row.name,
                version=payload.version,
                compose_path=compose_path,
                compose_paths=compose_paths,
                secret_refs=list(env_row.secret_refs or []),
                target_options=target_options,
                two_factor_code=payload.two_factor_code,
            )
        except TwoFactorError as exc:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={"code": exc.code, "reason": str(exc)},
            ) from exc
        except OrchestratorError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN
                if exc.code == "deploy.policy_denied"
                else status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": exc.code, "reason": str(exc)},
            ) from exc
        except DeployTargetError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": exc.code, "reason": str(exc)},
            ) from exc

    # Emit a notification regardless of channel — the in-memory ring
    # buffer is always populated so the web bell sees it.
    is_success = result.status == DeployStatusKind.SUCCESS
    await notifications.emit(
        kind=NotificationKind.DEPLOY_SUCCESS if is_success else NotificationKind.DEPLOY_FAILED,
        title=f"Deploy {result.status.value}: {env_row.name}",
        body=(
            f"Project={env_row.project_id} version={payload.version}"
            + (f" exec_code={result.exec_code}" if result.exec_code else "")
        ),
        actor_id=user.id,
        project_id=env_row.project_id,
        severity="info" if is_success else "error",
        details={"run_id": result.run_id, "env_id": env_row.id},
    )

    # Record an audit-grade `DeployRun` for every deploy attempt
    # (success or failure). `env.last_run` is updated on success
    # inside the helper so the UI's "current URL" reflects the
    # latest good state.
    await _record_deploy_run(
        db,
        env_row=env_row,
        version=payload.version,
        status_value=result.status.value,
        bound_url=result.bound_url,
        exec_code=result.exec_code,
        log_tail=result.log,
        actor_id=user.id,
        trigger_kind="manual",
    )
    await db.commit()

    return DeployResultResponse(
        run_id=result.run_id,
        status=result.status.value,
        exec_code=result.exec_code,
        log=result.log,
        bound_url=result.bound_url,
    )


@router.post(
    "/{env_id}/rollback",
    response_model=RollbackResultResponse,
    status_code=status.HTTP_200_OK,
)
async def trigger_rollback(
    env_id: str,
    payload: RollbackRequestBody,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    two_factor: TwoFactorVerifier = Depends(get_two_factor_verifier),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> RollbackResultResponse:
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    target = _build_target(env_row.deploy_target_kind, settings)
    orchestrator = DeployOrchestrator(
        policy_engine=policy_engine,
        target=target,
        audit_sink=audit_sink,
        two_factor=two_factor,
    )
    compose_cfg = (
        env_row.deploy_target_config if isinstance(env_row.deploy_target_config, dict) else {}
    )
    compose_path = compose_cfg.get("compose_path", "docker-compose.yml")
    raw_paths = compose_cfg.get("compose_paths") or []
    compose_paths: list[str] = [p for p in raw_paths if isinstance(p, str)]
    target_options: dict[str, Any] = {
        **(env_row.deploy_target_config or {}),
        **payload.target_options,
    }

    async with _lock_for(env_id):
        try:
            result = await orchestrator.rollback(
                actor_id=user.id,
                project_id=env_row.project_id,
                environment_id=env_row.id,
                environment_name=env_row.name,
                run_id=payload.run_id,
                compose_path=compose_path,
                compose_paths=compose_paths,
                target_options=target_options,
                to_version=payload.to_version,
                two_factor_code=payload.two_factor_code,
            )
        except TwoFactorError as exc:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={"code": exc.code, "reason": str(exc)},
            ) from exc
        except OrchestratorError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN
                if exc.code == "deploy.policy_denied"
                else status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": exc.code, "reason": str(exc)},
            ) from exc

    # History entry — rollback success rewrites `last_run` to the
    # restored version so the UI shows the post-rollback state as
    # current. Failed rollbacks leave `last_run` alone.
    await _record_deploy_run(
        db,
        env_row=env_row,
        version=result.restored_version or payload.to_version,
        status_value=result.status.value,
        bound_url=None,  # rollback target's adapter doesn't always re-route
        exec_code=result.exec_code,
        log_tail=result.log,
        actor_id=user.id,
        trigger_kind="rollback",
    )
    await db.commit()

    return RollbackResultResponse(
        run_id=result.run_id,
        status=result.status.value,
        restored_version=result.restored_version,
        exec_code=result.exec_code,
        log=result.log,
    )


# ──────────────────────────────────────────────── live log stream ──


@router.post("/{env_id}/deploy/stream")
async def trigger_deploy_stream(
    env_id: str,
    payload: DeployRequestBody,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    two_factor: TwoFactorVerifier = Depends(get_two_factor_verifier),  # noqa: B008
    notifications: NotificationService = Depends(get_notifications),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> StreamingResponse:
    """Run a deploy and stream progress as Server-Sent Events.

    Three frame kinds the UI listens for:
      * `{"type": "log", "content": "..."}` — incremental log tail.
      * `{"type": "status", "status": "running"|"success"|...}`.
      * `{"type": "done", "result": {run_id, status, bound_url, log,
        exec_code}}` — final result. Stream closes after.

    Implementation: start `orchestrator.deploy()` as a background task,
    poll the LocalComposeTarget's internal `_runs` state every 500 ms,
    emit deltas. Falls back to a single "done" frame for non-Local
    targets (SSH/webhook return synchronously with no in-progress log
    state, so streaming would only emit the final result anyway)."""
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    target = _build_target(env_row.deploy_target_kind, settings)
    orchestrator = DeployOrchestrator(
        policy_engine=policy_engine,
        target=target,
        audit_sink=audit_sink,
        two_factor=two_factor,
    )
    compose_cfg = (
        env_row.deploy_target_config if isinstance(env_row.deploy_target_config, dict) else {}
    )
    compose_path = compose_cfg.get("compose_path", "docker-compose.yml")
    raw_paths = compose_cfg.get("compose_paths") or []
    compose_paths: list[str] = [p for p in raw_paths if isinstance(p, str)]
    target_options: dict[str, Any] = {
        **(env_row.deploy_target_config or {}),
        **payload.target_options,
    }

    project_id = env_row.project_id
    env_name = env_row.name
    env_id_local = env_row.id
    actor_id = user.id
    version = payload.version

    async def stream():  # type: ignore[no-untyped-def]
        lock = _lock_for(env_id)
        async with lock:
            deploy_task = asyncio.create_task(
                orchestrator.deploy(
                    actor_id=actor_id,
                    project_id=project_id,
                    environment_id=env_id_local,
                    environment_name=env_name,
                    version=version,
                    compose_path=compose_path,
                    compose_paths=compose_paths,
                    secret_refs=list(env_row.secret_refs or []),
                    target_options=target_options,
                    two_factor_code=payload.two_factor_code,
                )
            )
            try:
                last_log = ""
                last_status = ""
                # Wait briefly for the target to register a run_id (Local
                # only — other targets don't surface in-progress state).
                run_id: str | None = None
                for _ in range(40):  # ~4s ceiling
                    if isinstance(target, LocalComposeTarget) and target._runs:
                        run_id = next(iter(target._runs.keys()))
                        break
                    if deploy_task.done():
                        break
                    await asyncio.sleep(0.1)

                while not deploy_task.done():
                    if (
                        run_id is not None
                        and isinstance(target, LocalComposeTarget)
                        and run_id in target._runs
                    ):
                        state = target._runs[run_id]
                        if state.log_tail != last_log:
                            delta = state.log_tail[len(last_log):]
                            last_log = state.log_tail
                            if delta.strip():
                                yield (
                                    f"data: {json.dumps({'type': 'log', 'content': delta})}\n\n"
                                ).encode()
                        if state.status.value != last_status:
                            last_status = state.status.value
                            yield (
                                f"data: {json.dumps({'type': 'status', 'status': last_status})}\n\n"
                            ).encode()
                    await asyncio.sleep(0.5)

                # Capture the final result. Surface any orchestrator-
                # raised error as a single status=failed frame so the
                # UI doesn't see a silent close.
                try:
                    result = await deploy_task
                except (TwoFactorError, OrchestratorError, DeployTargetError) as exc:
                    yield (
                        f"data: {json.dumps({'type': 'done', 'result': {'status': 'failed', 'exec_code': exc.code, 'log': str(exc), 'run_id': '', 'bound_url': None}})}\n\n"
                    ).encode()
                    return

                # Persist history + notify (same shape as the sync
                # POST /deploy endpoint via `_record_deploy_run`).
                is_success = result.status == DeployStatusKind.SUCCESS
                await _record_deploy_run(
                    db,
                    env_row=env_row,
                    version=version,
                    status_value=result.status.value,
                    bound_url=result.bound_url,
                    exec_code=result.exec_code,
                    log_tail=result.log,
                    actor_id=actor_id,
                    trigger_kind="manual",
                )
                await db.commit()
                await notifications.emit(
                    kind=NotificationKind.DEPLOY_SUCCESS
                    if is_success
                    else NotificationKind.DEPLOY_FAILED,
                    title=f"Deploy {result.status.value}: {env_name}",
                    body=f"Project={project_id} version={version}",
                    actor_id=actor_id,
                    project_id=project_id,
                    severity="info" if is_success else "error",
                    details={"run_id": result.run_id, "env_id": env_id_local},
                )
                yield (
                    f"data: {json.dumps({'type': 'done', 'result': {'run_id': result.run_id, 'status': result.status.value, 'bound_url': result.bound_url, 'log': result.log, 'exec_code': result.exec_code}})}\n\n"
                ).encode()
            finally:
                if not deploy_task.done():
                    deploy_task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────────────────────── run history ──


class DeployRunResponse(BaseModel):
    """One row in the env's deploy history. Mirrors `models.DeployRun`."""

    id: str
    environment_id: str
    version: str
    status: str
    bound_url: str | None = None
    exec_code: str | None = None
    log_tail: str = ""
    started_at: datetime
    finished_at: datetime | None = None
    actor_id: str | None = None
    trigger_kind: str = "manual"


@router.get(
    "/{env_id}/runs",
    response_model=list[DeployRunResponse],
)
async def list_deploy_runs(
    env_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[DeployRunResponse]:
    """Return the env's deploy history, newest first. Caps at 20 by
    default so the UI's list view doesn't pull a year of churn on
    every render. Used by the Rollback picker too — clicking
    "Rollback" picks a row from this list."""
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    limit = max(1, min(int(limit), 100))
    rows = (
        await db.execute(
            select(models.DeployRun)
            .where(models.DeployRun.environment_id == env_id)
            .order_by(models.DeployRun.started_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        DeployRunResponse(
            id=r.id,
            environment_id=r.environment_id,
            version=r.version,
            status=r.status,
            bound_url=r.bound_url,
            exec_code=r.exec_code,
            log_tail=r.log_tail or "",
            started_at=r.started_at,
            finished_at=r.finished_at,
            actor_id=r.actor_id,
            trigger_kind=r.trigger_kind,
        )
        for r in rows
    ]


# Keep `datetime` / `DeployStatusKind` imports alive for pydantic's
# runtime introspection and downstream router authors that grep
# this file. Tuple keeps the names referenced without re-binding `_`.
_USED: tuple[type, type] = (datetime, DeployStatusKind)


# ─────────────────────────────────────────── async deploy v2 ──
#
# The endpoints below are the **persistent** deploy flow:
#   POST /environments/{env_id}/deploy/async
#       → registers a background deploy task, returns {run_id}
#       immediately. The task survives across HTTP requests.
#   GET  /environments/{env_id}/deploy/active
#       → if a run is currently in flight for this env, returns it
#       (used by the frontend to auto-resume after tab navigation).
#   GET  /deploy/runs/{run_id}
#       → run detail snapshot.
#   GET  /deploy/runs/{run_id}/stream
#       → SSE that replays the captured log history, then live-tails
#       new content, then closes on `done`.
#   POST /deploy/runs/{run_id}/cancel
#       → cancel a running deploy.


class AsyncDeployAcceptedResponse(BaseModel):
    run_id: str
    environment_id: str
    status: str
    started_at: datetime


class ActiveRunResponse(BaseModel):
    run_id: str
    environment_id: str
    project_id: str
    version: str
    status: str
    started_at: datetime
    bound_url: str | None = None
    exec_code: str | None = None
    finished_at: datetime | None = None


# Dedicated router under `/api` for `/deploy/runs/{id}/...` endpoints
# that aren't scoped to an env path.
runs_router = APIRouter(prefix="/api/deploy", tags=["deploy"])


def _handle_to_active(handle: DeployRunHandle) -> ActiveRunResponse:
    return ActiveRunResponse(
        run_id=handle.run_id,
        environment_id=handle.environment_id,
        project_id=handle.project_id,
        version=handle.version,
        status=handle.status,
        started_at=handle.started_at_wallclock,
        bound_url=handle.bound_url,
        exec_code=handle.exec_code,
        finished_at=handle.finished_at,
    )


@router.post(
    "/{env_id}/deploy/async",
    response_model=AsyncDeployAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_deploy_async(
    env_id: str,
    payload: DeployRequestBody,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    two_factor: TwoFactorVerifier = Depends(get_two_factor_verifier),  # noqa: B008
    notifications: NotificationService = Depends(get_notifications),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    registry: DeployRegistry = Depends(get_deploy_registry),  # noqa: B008
) -> AsyncDeployAcceptedResponse:
    """Start a deploy as a process-scoped background task. Returns
    `202 Accepted` with the new run_id; the caller follows up by
    subscribing to `GET /api/deploy/runs/{run_id}/stream` for live
    log + status. The task keeps running even if the HTTP client
    disconnects — that's the whole point of moving away from the
    old `POST /deploy/stream` which tied the run to the request."""
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    target = _build_target(env_row.deploy_target_kind, settings)
    orchestrator = DeployOrchestrator(
        policy_engine=policy_engine,
        target=target,
        audit_sink=audit_sink,
        two_factor=two_factor,
    )
    compose_cfg = (
        env_row.deploy_target_config if isinstance(env_row.deploy_target_config, dict) else {}
    )
    compose_path = compose_cfg.get("compose_path", "docker-compose.yml")
    raw_paths = compose_cfg.get("compose_paths") or []
    compose_paths: list[str] = [p for p in raw_paths if isinstance(p, str)]
    target_options: dict[str, Any] = {
        **(env_row.deploy_target_config or {}),
        **payload.target_options,
    }
    actor_id = user.id
    version = payload.version
    project_id = env_row.project_id
    env_name = env_row.name
    secret_refs = list(env_row.secret_refs or [])
    two_factor_code = payload.two_factor_code

    # The runner closure is what the registry awaits in its
    # background task. It runs the orchestrator + a log pump in
    # parallel, then settles results onto the handle.
    async def runner(handle: DeployRunHandle):  # type: ignore[no-untyped-def]
        deploy_coro = orchestrator.deploy(
            actor_id=actor_id,
            project_id=project_id,
            environment_id=env_id,
            environment_name=env_name,
            version=version,
            compose_path=compose_path,
            compose_paths=compose_paths,
            secret_refs=secret_refs,
            target_options=target_options,
            two_factor_code=two_factor_code,
        )
        # LocalComposeTarget exposes per-run state we can poll for
        # log deltas while the deploy is in flight. Other targets
        # (SSH / webhook) return synchronously without surfacing a
        # log tail — for those we just await the result.
        if isinstance(target, LocalComposeTarget):
            pump = make_log_pump(handle, target)
            pump_task = asyncio.create_task(pump(), name=f"deploy-log-pump-{handle.run_id}")
            try:
                result = await deploy_coro
            finally:
                if not pump_task.done():
                    pump_task.cancel()
                    try:
                        await pump_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
        else:
            result = await deploy_coro
            # Splice the synchronous result.log into the handle.
            if result.log:
                handle.append_log(result.log)

        is_success = result.status == DeployStatusKind.SUCCESS
        # Notifications & env-cache update — done here (inside the
        # task) so they fire regardless of whether the SSE client is
        # still connected.
        try:
            await notifications.emit(
                kind=NotificationKind.DEPLOY_SUCCESS
                if is_success
                else NotificationKind.DEPLOY_FAILED,
                title=f"Deploy {result.status.value}: {env_name}",
                body=f"Project={project_id} version={version}",
                actor_id=actor_id,
                project_id=project_id,
                severity="info" if is_success else "error",
                details={"run_id": handle.run_id, "env_id": env_id},
            )
        except Exception:  # noqa: BLE001
            logger.exception("deploy.notify_failed", run_id=handle.run_id)
        return result

    try:
        handle = await registry.start(
            env_row=env_row,
            version=version,
            actor_id=actor_id,
            trigger_kind="manual",
            runner=runner,
        )
    except DeployAlreadyRunning as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "deploy.already_running",
                "reason": f"another deploy is in flight for this environment (run_id={exc})",
            },
        ) from exc

    return AsyncDeployAcceptedResponse(
        run_id=handle.run_id,
        environment_id=handle.environment_id,
        status=handle.status,
        started_at=handle.started_at_wallclock,
    )


@router.get(
    "/{env_id}/deploy/active",
    response_model=ActiveRunResponse | None,
)
async def get_active_deploy(
    env_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: DeployRegistry = Depends(get_deploy_registry),  # noqa: B008
) -> ActiveRunResponse | None:
    """Returns the in-flight run handle for the env (if any). The
    UI hits this on mount so a tab returning to the Deploy view
    knows to reconnect to the SSE stream rather than show the
    `idle` button. Returns `null` (not 404) when no run is active."""
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    handle = registry.get_active_for_env(env_id)
    if handle is None:
        return None
    return _handle_to_active(handle)


@runs_router.get("/runs/{run_id}", response_model=ActiveRunResponse)
async def get_run(
    run_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: DeployRegistry = Depends(get_deploy_registry),  # noqa: B008
) -> ActiveRunResponse:
    handle = registry.get(run_id)
    if handle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "deploy.run.not_found", "reason": run_id},
        )
    return _handle_to_active(handle)


class EnvConfigSnapshot(BaseModel):
    """Environment config surfaced alongside a run so the operator
    sees *what* got deployed. Today this is the CURRENT env config,
    not a snapshot at deploy-time — if config has changed since,
    surface the live values (good enough for the common "what is
    this URL serving" question). Future improvement: snapshot
    target_options into the DeployRun row at start."""

    id: str
    name: str
    deploy_target_kind: str
    deploy_target_config: dict[str, Any]
    require_2fa: bool
    secret_refs: list[str]
    cost_multiplier: float


class ProjectSnapshot(BaseModel):
    id: str
    slug: str
    display_name: str


class RunDetailResponse(BaseModel):
    """Full record of a single deploy run — used by the UI's
    'View details' panel for past runs. Joins DB DeployRun +
    Environment + Project so the client doesn't need three round-
    trips just to render one detail screen."""

    run: DeployRunResponse
    environment: EnvConfigSnapshot
    project: ProjectSnapshot


@runs_router.get("/runs/{run_id}/detail", response_model=RunDetailResponse)
async def get_run_detail(
    run_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> RunDetailResponse:
    """Loaded after a click on a history row. Returns the persisted
    DeployRun (always available; survives server restarts unlike the
    in-memory registry handle) plus the env + project for context."""
    run_row = await db.get(models.DeployRun, run_id)
    if run_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "deploy.run.not_found", "reason": run_id},
        )
    env_row = await db.get(models.Environment, run_row.environment_id)
    if env_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "environment.not_found", "reason": run_row.environment_id},
        )
    try:
        project_row = await fetch_project_for(
            db, actor=user, project_id=env_row.project_id
        )
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    return RunDetailResponse(
        run=DeployRunResponse(
            id=run_row.id,
            environment_id=run_row.environment_id,
            version=run_row.version,
            status=run_row.status,
            bound_url=run_row.bound_url,
            exec_code=run_row.exec_code,
            log_tail=run_row.log_tail or "",
            started_at=run_row.started_at,
            finished_at=run_row.finished_at,
            actor_id=run_row.actor_id,
            trigger_kind=run_row.trigger_kind,
        ),
        environment=EnvConfigSnapshot(
            id=env_row.id,
            name=env_row.name,
            deploy_target_kind=env_row.deploy_target_kind.value,
            deploy_target_config=dict(env_row.deploy_target_config or {}),
            require_2fa=env_row.require_2fa,
            secret_refs=list(env_row.secret_refs or []),
            cost_multiplier=float(env_row.cost_multiplier),
        ),
        project=ProjectSnapshot(
            id=project_row.id,
            slug=project_row.slug,
            display_name=project_row.display_name,
        ),
    )


@runs_router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: DeployRegistry = Depends(get_deploy_registry),  # noqa: B008
) -> StreamingResponse:
    """SSE feed for a run. Replays the log buffer up to the current
    point, then live-tails new content until the run terminates,
    then closes with a `done` frame. Multiple tabs may subscribe
    simultaneously.

    We use `StreamingResponse` over `EventSourceResponse` here
    because the broadcaster already emits raw SSE-formatted bytes
    (`event: name\\ndata: ...\\n\\n`) — no need to re-encode through
    sse-starlette's dict layer."""
    if registry.get(run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "deploy.run.not_found", "reason": run_id},
        )

    async def event_iter():  # type: ignore[no-untyped-def]
        # SSE retry hint — if the connection drops, browsers wait 3 s
        # before reconnecting (default is 3 s anyway; explicit is
        # nicer when staring at a flaky network).
        yield b"retry: 3000\n\n"
        try:
            async for frame in registry.subscribe(run_id):
                yield frame
        except DeployRunNotFound:
            return
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # nginx + Cloudflare honour this and stop buffering.
            "X-Accel-Buffering": "no",
        },
    )


@runs_router.post("/runs/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(
    run_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: DeployRegistry = Depends(get_deploy_registry),  # noqa: B008
) -> None:
    try:
        await registry.cancel(run_id)
    except DeployRunNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "deploy.run.not_found", "reason": run_id},
        ) from exc


# ─────────────────────── stack management (post-deploy) ──
#
# Once a prod stack is up, the operator needs to see / stop /
# restart it. The Performance dashboard surfaces per-container
# stats, but the Deploy view is the natural home for compose-
# project-level operations. These endpoints talk to a single
# `StackManager` keyed by environment_id (which derives the
# compose project name `gapt-prod-<env_id>` — same shape used by
# LocalComposeTarget).


_STACK_MANAGER: StackManager | None = None


def _get_stack_manager() -> StackManager:
    global _STACK_MANAGER  # noqa: PLW0603
    if _STACK_MANAGER is None:
        _STACK_MANAGER = StackManager(client=make_default_client())
    return _STACK_MANAGER


class StackServiceDto(BaseModel):
    container_id: str
    container_name: str
    service: str
    image: str
    status: str
    health: str | None
    started_at: str | None
    exit_code: int | None


class StackStatusResponse(BaseModel):
    environment_id: str
    project: str
    services: list[StackServiceDto]
    running_count: int
    total_count: int


class StackOpResponse(BaseModel):
    environment_id: str
    project: str
    action: str
    ok: bool
    affected: int
    output: str


@router.get("/{env_id}/stack", response_model=StackStatusResponse)
async def stack_status(
    env_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StackStatusResponse:
    """Live snapshot of every container in this env's prod compose
    stack. The frontend polls this every couple of seconds while
    the user is looking at a deploy detail page so action buttons
    reflect reality without a full page refresh."""
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    s = await _get_stack_manager().status(env_row.project_id)
    return StackStatusResponse(
        environment_id=env_id,
        project=s.project,
        services=[
            StackServiceDto(
                container_id=svc.container_id,
                container_name=svc.container_name,
                service=svc.service,
                image=svc.image,
                status=svc.status,
                health=svc.health,
                started_at=svc.started_at,
                exit_code=svc.exit_code,
            )
            for svc in s.services
        ],
        running_count=s.running_count,
        total_count=s.total_count,
    )


@router.post("/{env_id}/stack/down", response_model=StackOpResponse)
async def stack_down(
    env_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StackOpResponse:
    """`docker compose down --remove-orphans` for this env's prod
    stack. Containers + network are removed; volumes survive. To
    bring it back, the operator clicks Deploy again."""
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    result = await _get_stack_manager().stop(env_row.project_id)
    return StackOpResponse(
        environment_id=env_id,
        project=result.project,
        action=result.action,
        ok=result.ok,
        affected=result.affected,
        output=result.output,
    )


class StackRerouteBody(BaseModel):
    """Optional overrides for `POST /stack/reroute`. Both fields
    persist back to `Environment.deploy_target_config` so they
    survive future re-deploys."""

    primary_service: str | None = None
    primary_port: int | None = None
    strip_prefix: bool | None = None


@router.post("/{env_id}/stack/reroute", response_model=StackOpResponse)
async def stack_reroute(
    env_id: str,
    body: StackRerouteBody | None = None,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> StackOpResponse:
    """Re-register the env's Caddy preview route against the
    currently-running primary container. Use when:

      * The stack's compose file changed primary service / port
        and the old route now points at the wrong upstream.
      * The default routing policy changed (e.g. flipped to
        `strip_prefix=True` after a server upgrade) and the operator
        wants the new behaviour without a full re-deploy.

    Implementation: discover the running primary container by
    label, then call `LocalComposeTarget._route_primary_service`'s
    moral equivalent — register a fresh SubdomainBinding via the
    Caddy admin API (DELETE+POST atomically replaces the old)."""
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    from gapt_server.domains.caddy.admin_api import (  # noqa: PLC0415
        CaddyAdminClient,
        CaddyHttpTransport,
    )
    from gapt_server.domains.caddy.subdomain import (  # noqa: PLC0415
        PreviewMode,
        SubdomainBinding,
        SubdomainManager,
    )

    if not settings.caddy_admin_url or not settings.caddy_preview_domain:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "caddy.not_configured", "reason": "caddy admin url unset"},
        )

    # Find the primary container. Priority order:
    #
    #   1. Body override (the operator told us explicitly via the
    #      Reroute modal).
    #   2. Saved `target_options.primary_service` (set by a previous
    #      successful deploy).
    #   3. Reverse-proxy services — `nginx`, `proxy`, `gateway`,
    #      `traefik`, `caddy`, `envoy`. Compose stacks that ship one
    #      of these are almost always using it as the entry point
    #      that fans out to frontend + backend internally. Routing
    #      to the frontend container directly bypasses that fan-out
    #      and breaks any `/api/v1/*` XHRs the SPA makes.
    #   4. Frontend-named services as a sensible fallback for
    #      single-service stacks.
    #   5. First running container (last resort).
    target_config = env_row.deploy_target_config if isinstance(env_row.deploy_target_config, dict) else {}
    override = body or StackRerouteBody()
    primary_service = override.primary_service or target_config.get("primary_service")
    primary_port = override.primary_port or target_config.get("primary_port") or 3000

    project = f"gapt-prod-{env_row.project_id.lower()}"
    sm = _get_stack_manager()
    s = await sm.status(env_row.project_id)
    if s.total_count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "stack.not_running",
                "reason": "no containers running for this env — deploy first",
            },
        )

    reverse_proxy_names = {"nginx", "proxy", "gateway", "traefik", "caddy", "envoy"}
    chosen = next(
        (svc for svc in s.services if primary_service and svc.service == primary_service),
        None,
    ) or next(
        (svc for svc in s.services if svc.service in reverse_proxy_names),
        None,
    ) or next(
        (svc for svc in s.services if svc.service in {"frontend", "web", "app"}),
        None,
    ) or next(
        (svc for svc in s.services if svc.status == "running"),
        None,
    )
    if chosen is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "stack.no_primary",
                "reason": "could not identify a primary container in the stack",
            },
        )
    # If the heuristic picked a reverse-proxy service (or the
    # caller provided no explicit override), default the port to
    # 80 when the chosen service is a known reverse-proxy.
    if override.primary_port is None and not target_config.get("primary_port"):
        if chosen.service in reverse_proxy_names:
            primary_port = 80

    transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
    manager = SubdomainManager(
        client=CaddyAdminClient(transport=transport),
        preview_domain=settings.caddy_preview_domain,
    )
    mode_str = str(target_config.get("preview_mode", "path")).lower()
    mode = PreviewMode.SUBDOMAIN if mode_str == "subdomain" else PreviewMode.PATH
    strip_opt = target_config.get("strip_prefix")
    strip_prefix = (True if strip_opt is None else bool(strip_opt)) and mode == PreviewMode.PATH

    # Make sure the chosen upstream container is on gapt-net so
    # Caddy (which is also on gapt-net) can reach it by DNS name.
    # Idempotent — docker prints "already in network" on a
    # repeat, which we treat as success.
    import asyncio as _asyncio  # noqa: PLC0415

    connect = await _asyncio.create_subprocess_exec(
        "docker",
        "network",
        "connect",
        "gapt-net",
        chosen.container_name,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    _, connect_err_b = await connect.communicate()
    connect_err = connect_err_b.decode("utf-8", "replace").strip()
    # "already exists" / "already in" are both fine.
    network_ok = (
        connect.returncode == 0
        or "already" in connect_err.lower()
    )
    if not network_ok:
        logger.warning(
            "deploy.reroute.network_connect_failed",
            container=chosen.container_name,
            err=connect_err[:300],
        )

    slug = f"prod-{env_row.name}-{env_row.project_id}".lower()
    binding = SubdomainBinding(
        workspace_slug=slug,
        upstream_host=chosen.container_name,
        upstream_port=primary_port,
        mode=mode,
        strip_prefix=strip_prefix,
    )
    try:
        host = await manager.register(binding)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "caddy.register_failed", "reason": str(exc)},
        ) from exc

    # Persist the operator's choice back to the env so the next
    # deploy picks the same upstream + the next reroute (from a
    # different tab / after a server restart) sees it too. Without
    # this the heuristic would have to re-guess each time.
    new_config = dict(target_config)
    new_config["primary_service"] = chosen.service or chosen.container_name
    new_config["primary_port"] = int(primary_port)
    if override.strip_prefix is not None:
        new_config["strip_prefix"] = bool(override.strip_prefix)
    if new_config != target_config:
        env_row.deploy_target_config = new_config
        await db.commit()

    return StackOpResponse(
        environment_id=env_id,
        project=project,
        action="reroute",
        ok=True,
        affected=1,
        output=(
            f"re-routed → https://{host}\n"
            f"upstream={chosen.container_name}:{primary_port} "
            f"(service={chosen.service or '?'})\n"
            f"strip_prefix={strip_prefix} mode={mode.value}\n"
            f"network_connect={'ok' if network_ok else 'failed'}"
        ),
    )


@router.post("/{env_id}/stack/restart", response_model=StackOpResponse)
async def stack_restart(
    env_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StackOpResponse:
    """`docker compose -p <project> restart` — bounces every
    container in the stack in place. Useful after an env-file
    change without rebuilding."""
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    result = await _get_stack_manager().restart(env_row.project_id)
    return StackOpResponse(
        environment_id=env_id,
        project=result.project,
        action=result.action,
        ok=result.ok,
        affected=result.affected,
        output=result.output,
    )
