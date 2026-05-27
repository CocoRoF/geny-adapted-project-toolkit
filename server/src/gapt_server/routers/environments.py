"""Environments CRUD — `environments` table control plane.

`Environment` rows are *deploy targets* a project can ship to:
local-compose / remote-ssh / webhook (k8s lands in M4). Each row
carries the target kind + target-specific config + policy hints
(`require_2fa`, `cost_multiplier`). The DeployOrchestrator
(`routers/deploy.py`) reads these rows to know where to send a
release.

Endpoints (project-scoped):

- `GET    /_gapt/api/projects/{pid}/environments`
- `POST   /_gapt/api/projects/{pid}/environments`
- `GET    /_gapt/api/environments/{eid}`
- `PUT    /_gapt/api/environments/{eid}`
- `DELETE /_gapt/api/environments/{eid}`

Single-admin model — every authenticated request goes through
`fetch_project_for` purely to surface a clean 404 on bogus
project_ids; there is no role hierarchy.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic at runtime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from gapt_server.container import get_audit_sink, get_db_session
from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import AuditEvent, AuditSink
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


by_project = APIRouter(prefix="/_gapt/api/projects", tags=["environments"])
by_id = APIRouter(prefix="/_gapt/api/environments", tags=["environments"])


class EnvironmentPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=80)
    deploy_target_kind: enums.DeployTargetKind
    deploy_target_config: dict[str, Any] = Field(default_factory=dict)
    require_2fa: bool = False
    secret_refs: list[str] = Field(default_factory=list)
    cost_multiplier: float = Field(default=1.0, ge=0.0, le=100.0)
    hooks: dict[str, Any] = Field(default_factory=dict)


class EnvironmentResponse(EnvironmentPayload):
    id: str
    project_id: str
    created_at: datetime
    # Last deploy summary — {run_id, status, bound_url, deployed_at,
    # version} from the most recent successful deploy. Empty dict
    # when the env has never been deployed.
    last_run: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_row(cls, row: models.Environment) -> EnvironmentResponse:
        return cls(
            id=row.id,
            project_id=row.project_id,
            name=row.name,
            deploy_target_kind=row.deploy_target_kind,
            deploy_target_config=dict(row.deploy_target_config or {}),
            require_2fa=row.require_2fa,
            secret_refs=list(row.secret_refs or []),
            cost_multiplier=float(row.cost_multiplier),
            hooks=dict(row.hooks or {}),
            last_run=dict(row.last_run or {}),
            created_at=row.created_at,
        )


async def _env_with_fallback(
    db: AsyncSession, row: models.Environment
) -> EnvironmentResponse:
    """`EnvironmentResponse.from_row` plus a "best available
    `last_run`" backfill: when the cached `env.last_run` blob is
    empty or missing a success run id, fall back to the most-recent
    successful `DeployRun` row. Without this the UI shows "no
    deploys" even though the stack is still serving — happens for
    envs that pre-date the cache or after an earlier code path
    forgot to write the cache."""
    resp = EnvironmentResponse.from_row(row)
    has_run = (
        bool(resp.last_run.get("run_id"))
        and resp.last_run.get("status") == "success"
    )
    # "stopped" is an explicit terminal state set by stack/down —
    # we respect it (no fallback) so the sidebar doesn't bounce
    # back to LIVE after the operator just stopped the stack.
    if resp.last_run.get("status") == "stopped":
        return resp
    if has_run:
        return resp
    latest = (
        await db.execute(
            select(models.DeployRun)
            .where(
                models.DeployRun.environment_id == row.id,
                models.DeployRun.status == "success",
            )
            .order_by(models.DeployRun.finished_at.desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is not None:
        resp.last_run = {
            "run_id": latest.id,
            "status": latest.status,
            "bound_url": latest.bound_url,
            "version": latest.version,
            "deployed_at": (
                latest.finished_at.isoformat() if latest.finished_at else None
            ),
            "trigger_kind": latest.trigger_kind,
        }
    return resp


async def _row_or_404(db: AsyncSession, env_id: str) -> models.Environment:
    row = (
        await db.execute(
            select(models.Environment).where(models.Environment.id == env_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "environment.not_found", "reason": env_id},
        )
    return row


@by_project.get(
    "/{project_id}/environments", response_model=list[EnvironmentResponse]
)
async def list_environments(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[EnvironmentResponse]:
    try:
        await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    rows = (
        await db.execute(
            select(models.Environment)
            .where(models.Environment.project_id == project_id)
            .order_by(models.Environment.created_at.asc())
        )
    ).scalars().all()
    out: list[EnvironmentResponse] = []
    for r in rows:
        out.append(await _env_with_fallback(db, r))
    return out


@by_project.post(
    "/{project_id}/environments",
    response_model=EnvironmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_environment(
    project_id: str,
    payload: EnvironmentPayload,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> EnvironmentResponse:
    try:
        await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    # Uniqueness check (name within project) — better error than a
    # raw integrity violation from the DB constraint.
    existing = (
        await db.execute(
            select(models.Environment).where(
                (models.Environment.project_id == project_id)
                & (models.Environment.name == payload.name)
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "environment.duplicate",
                "reason": f"project already has environment {payload.name!r}",
            },
        )

    row = models.Environment(
        id=new_ulid(),
        project_id=project_id,
        name=payload.name,
        deploy_target_kind=payload.deploy_target_kind,
        deploy_target_config=payload.deploy_target_config,
        require_2fa=payload.require_2fa,
        secret_refs=payload.secret_refs,
        cost_multiplier=payload.cost_multiplier,
        hooks=payload.hooks,
    )
    db.add(row)
    await db.flush()
    await audit_sink.log(
        AuditEvent(
            action="environment.create",
            actor_type=enums.AuditActorType.USER,
            actor_id=user.id,
            outcome=enums.AuditOutcome.OK,
            scope={"project_id": project_id, "environment_id": row.id},
            subject={
                "name": row.name,
                "deploy_target_kind": row.deploy_target_kind.value,
            },
        )
    )
    await db.commit()
    return EnvironmentResponse.from_row(row)


@by_id.get("/{env_id}", response_model=EnvironmentResponse)
async def get_environment(
    env_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> EnvironmentResponse:
    row = await _row_or_404(db, env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return await _env_with_fallback(db, row)


@by_id.put("/{env_id}", response_model=EnvironmentResponse)
async def update_environment(
    env_id: str,
    payload: EnvironmentPayload,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> EnvironmentResponse:
    row = await _row_or_404(db, env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    # `name` is the only field a uniqueness constraint cares about —
    # if it's changing, verify it doesn't collide with a sibling env.
    if payload.name != row.name:
        existing = (
            await db.execute(
                select(models.Environment).where(
                    (models.Environment.project_id == row.project_id)
                    & (models.Environment.name == payload.name)
                    & (models.Environment.id != env_id)
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "environment.duplicate",
                    "reason": f"environment {payload.name!r} already exists in this project",
                },
            )

    row.name = payload.name
    row.deploy_target_kind = payload.deploy_target_kind
    row.deploy_target_config = payload.deploy_target_config
    row.require_2fa = payload.require_2fa
    row.secret_refs = payload.secret_refs
    row.cost_multiplier = payload.cost_multiplier
    row.hooks = payload.hooks
    await db.flush()
    await audit_sink.log(
        AuditEvent(
            action="environment.update",
            actor_type=enums.AuditActorType.USER,
            actor_id=user.id,
            outcome=enums.AuditOutcome.OK,
            scope={"project_id": row.project_id, "environment_id": row.id},
            subject={"name": row.name},
        )
    )
    await db.commit()
    return EnvironmentResponse.from_row(row)


@by_id.delete("/{env_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_environment(
    env_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> None:
    row = await _row_or_404(db, env_id)
    try:
        await fetch_project_for(db, actor=user, project_id=row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    await db.delete(row)
    await audit_sink.log(
        AuditEvent(
            action="environment.delete",
            actor_type=enums.AuditActorType.USER,
            actor_id=user.id,
            outcome=enums.AuditOutcome.OK,
            scope={"project_id": row.project_id, "environment_id": env_id},
        )
    )
    await db.commit()
