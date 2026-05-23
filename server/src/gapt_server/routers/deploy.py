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
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import (
    get_audit_sink,
    get_db_session,
    get_notifications,
    get_policy_engine,
)
from gapt_server.db import enums, models
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001
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


class RollbackResultResponse(BaseModel):
    run_id: str
    status: str
    restored_version: str | None = None
    exec_code: str | None = None
    log: str = ""


# ──────────────────────────────────────────── target factory ──


def _build_target(kind: enums.DeployTargetKind) -> DeployTarget:
    if kind == enums.DeployTargetKind.LOCAL:
        return LocalComposeTarget()
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


@router.post(
    "/{env_id}/deploy",
    response_model=DeployResultResponse,
    status_code=status.HTTP_200_OK,
)
async def trigger_deploy(
    env_id: str,
    payload: DeployRequestBody,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    two_factor: TwoFactorVerifier = Depends(get_two_factor_verifier),  # noqa: B008
    notifications: NotificationService = Depends(get_notifications),  # noqa: B008
) -> DeployResultResponse:
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    target = _build_target(env_row.deploy_target_kind)
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

    return DeployResultResponse(
        run_id=result.run_id,
        status=result.status.value,
        exec_code=result.exec_code,
        log=result.log,
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
    user: models.User = Depends(get_current_user),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
    two_factor: TwoFactorVerifier = Depends(get_two_factor_verifier),  # noqa: B008
) -> RollbackResultResponse:
    env_row = await _resolve_env(db, env_id=env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=env_row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    target = _build_target(env_row.deploy_target_kind)
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

    return RollbackResultResponse(
        run_id=result.run_id,
        status=result.status.value,
        restored_version=result.restored_version,
        exec_code=result.exec_code,
        log=result.log,
    )


# Keep `datetime` / `DeployStatusKind` imports alive for pydantic's
# runtime introspection and downstream router authors that grep
# this file. Tuple keeps the names referenced without re-binding `_`.
_USED: tuple[type, type] = (datetime, DeployStatusKind)
