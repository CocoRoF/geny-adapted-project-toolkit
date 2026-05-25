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
