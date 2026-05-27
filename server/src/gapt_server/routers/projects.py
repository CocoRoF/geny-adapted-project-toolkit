"""Project + Environment routes.

- `POST   /_gapt/api/projects`                              — create
- `GET    /_gapt/api/projects`                              — list
- `GET    /_gapt/api/projects/{pid}`                        — fetch
- `PATCH  /_gapt/api/projects/{pid}`                        — update
- `DELETE /_gapt/api/projects/{pid}`                        — archive
- `POST   /_gapt/api/projects/{pid}/environments`           — create env
- `GET    /_gapt/api/projects/{pid}/environments`           — list envs
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  — pydantic runtime introspection
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from gapt_server.container import get_audit_sink, get_db_session
from gapt_server.db import enums
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.projects.service import (
    EnvironmentView,
    ProjectError,
    ProjectService,
    ProjectView,
)
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$"


def get_project_service(
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> ProjectService:
    return ProjectService(audit_sink=audit_sink)


router = APIRouter(prefix="/_gapt/api/projects", tags=["projects"])


# ────────────────────────────────────────────────────── DTOs ──


class CreateProjectRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120, pattern=_SLUG_PATTERN)
    display_name: str = Field(min_length=1, max_length=200)
    git_remote_url: str = Field(min_length=4, max_length=2048)
    git_provider: enums.GitProvider = enums.GitProvider.GITHUB
    default_compose_paths: list[str] = Field(default_factory=list)
    compose_profile_dev: str | None = None
    compose_profile_prod: str | None = None
    git_auth_secret_ref: str | None = None


class UpdateProjectRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    default_compose_paths: list[str] | None = None
    compose_profile_dev: str | None = None
    compose_profile_prod: str | None = None


class ProjectResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    git_remote_url: str
    git_provider: enums.GitProvider
    default_compose_paths: list[str]
    compose_profile_dev: str | None
    compose_profile_prod: str | None
    created_at: datetime
    archived_at: datetime | None

    @classmethod
    def from_view(cls, v: ProjectView) -> ProjectResponse:
        return cls(**v.__dict__)


class CreateEnvironmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    deploy_target_kind: enums.DeployTargetKind
    deploy_target_config: dict[str, Any] = Field(default_factory=dict)
    require_2fa: bool = False
    secret_refs: list[str] = Field(default_factory=list)
    cost_multiplier: float = Field(default=1.0, ge=0)
    hooks: dict[str, Any] = Field(default_factory=dict)


class EnvironmentResponse(BaseModel):
    id: str
    project_id: str
    name: str
    deploy_target_kind: enums.DeployTargetKind
    deploy_target_config: dict[str, Any]
    require_2fa: bool
    secret_refs: list[str]
    cost_multiplier: float
    hooks: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_view(cls, v: EnvironmentView) -> EnvironmentResponse:
        return cls(**v.__dict__)


# ─────────────────────────────────────────────────── helpers ──


def http_from_project_error(exc: ProjectError) -> HTTPException:
    if exc.code in {"project.not_found"}:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "reason": str(exc)},
        )
    if exc.code in {"project.forbidden", "project.role_insufficient"}:
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "reason": str(exc)},
        )
    if exc.code in {"project.slug_taken", "environment.name_taken"}:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "reason": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": exc.code, "reason": str(exc)},
    )


# ───────────────────────────────────────────────── endpoints ──


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: ProjectService = Depends(get_project_service),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ProjectResponse:
    try:
        view = await svc.create(
            db,
            actor=user,
            slug=payload.slug,
            display_name=payload.display_name,
            git_remote_url=payload.git_remote_url,
            git_provider=payload.git_provider,
            default_compose_paths=payload.default_compose_paths,
            compose_profile_dev=payload.compose_profile_dev,
            compose_profile_prod=payload.compose_profile_prod,
            git_auth_secret_ref=payload.git_auth_secret_ref,
        )
        await db.commit()
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return ProjectResponse.from_view(view)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: ProjectService = Depends(get_project_service),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[ProjectResponse]:
    views = await svc.list_for_user(
        db, actor=user, include_archived=include_archived
    )
    return [ProjectResponse.from_view(v) for v in views]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: ProjectService = Depends(get_project_service),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ProjectResponse:
    try:
        view = await svc.get(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return ProjectResponse.from_view(view)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: UpdateProjectRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: ProjectService = Depends(get_project_service),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ProjectResponse:
    try:
        view = await svc.update(
            db,
            actor=user,
            project_id=project_id,
            display_name=payload.display_name,
            default_compose_paths=payload.default_compose_paths,
            compose_profile_dev=payload.compose_profile_dev,
            compose_profile_prod=payload.compose_profile_prod,
        )
        await db.commit()
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return ProjectResponse.from_view(view)


@router.delete("/{project_id}", response_model=ProjectResponse)
async def archive_project(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: ProjectService = Depends(get_project_service),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ProjectResponse:
    try:
        view = await svc.archive(db, actor=user, project_id=project_id)
        await db.commit()
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return ProjectResponse.from_view(view)


@router.post(
    "/{project_id}/environments",
    response_model=EnvironmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_environment(
    project_id: str,
    payload: CreateEnvironmentRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: ProjectService = Depends(get_project_service),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> EnvironmentResponse:
    try:
        view = await svc.create_environment(
            db,
            actor=user,
            project_id=project_id,
            name=payload.name,
            deploy_target_kind=payload.deploy_target_kind,
            deploy_target_config=payload.deploy_target_config,
            require_2fa=payload.require_2fa,
            secret_refs=payload.secret_refs,
            cost_multiplier=payload.cost_multiplier,
            hooks=payload.hooks,
        )
        await db.commit()
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return EnvironmentResponse.from_view(view)


@router.get("/{project_id}/environments", response_model=list[EnvironmentResponse])
async def list_environments(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: ProjectService = Depends(get_project_service),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[EnvironmentResponse]:
    try:
        views = await svc.list_environments(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return [EnvironmentResponse.from_view(v) for v in views]
