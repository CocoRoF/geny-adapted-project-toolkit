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

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import (
    AppContainer,
    get_app_settings,
    get_audit_sink,
    get_container,
    get_db_session,
)
from gapt_server.db import enums, models
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

    from gapt_server.domains.workspaces.service import WorkspaceService
    from gapt_server.settings import Settings

logger = structlog.get_logger(__name__)

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


async def _cascade_archive_cleanup(
    db: AsyncSession,
    *,
    actor: AdminPrincipal,
    project_id: str,
    workspace_svc: WorkspaceService,
    settings: Settings,
) -> dict[str, int]:
    """Cascade teardown when a project is archived.

    Without this, archiving a project only sets `archived_at` on the
    row — every workspace container, prod compose stack, ServiceRegistry
    entry, and Caddy preview route keeps running. The user "deleted"
    the project but the host still has 6-9 containers, an open port,
    and a live Caddy route per environment.

    Cleanup is best-effort per resource — one failed teardown
    shouldn't block the rest, and the operator can always finish the
    job via the Performance dashboard's Orphan cleanup modal.

    Order matters:
      1. Workspaces first (their containers may be the upstream that
         Caddy routes point at — kill the route after the upstream,
         not before, so any in-flight request fails cleanly with a
         502 instead of a misroute).
      2. Prod compose stacks (independent — keyed by project_id).
      3. Caddy preview routes (one per workspace, one per env that
         had a deploy)."""
    # Lazy import to avoid pulling deploy-stack/caddy machinery into
    # the projects-router import graph during tests that don't need them.
    from gapt_server.domains.caddy.admin_api import (  # noqa: PLC0415
        CaddyAdminClient,
        CaddyAdminError,
        CaddyHttpTransport,
    )
    from gapt_server.domains.caddy.subdomain import (  # noqa: PLC0415
        SubdomainManager,
    )
    from gapt_server.domains.deploy.stack_manager import StackManager  # noqa: PLC0415
    from gapt_server.domains.sandbox import make_default_client  # noqa: PLC0415

    counts = {"workspaces": 0, "stacks": 0, "caddy_routes": 0}

    # ── 1. Workspaces ──
    workspaces = (
        await db.execute(
            select(models.Workspace).where(
                models.Workspace.project_id == project_id,
                models.Workspace.status != enums.WorkspaceStatus.ARCHIVED,
            )
        )
    ).scalars().all()
    for ws in workspaces:
        try:
            await workspace_svc.delete(db, actor=actor, workspace_id=ws.id)
            counts["workspaces"] += 1
        except Exception as exc:  # noqa: BLE001
            # Container already removed / sandbox row gone / docker
            # daemon hiccup — log and keep going. The next pass through
            # Orphan cleanup will pick up whatever's left.
            logger.warning(
                "project.archive.workspace_cleanup_failed",
                project_id=project_id,
                workspace_id=ws.id,
                error=str(exc),
            )

    # ── 2. Prod compose stack ──
    # StackManager keys by project_id (a single stack per project,
    # shared across that project's envs). One `down` call is enough.
    try:
        sm = StackManager(client=make_default_client())
        result = await sm.stop(project_id)
        if result.ok:
            counts["stacks"] = 1
        else:
            logger.warning(
                "project.archive.stack_stop_nonok",
                project_id=project_id,
                tail=result.output[-200:],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project.archive.stack_stop_failed",
            project_id=project_id,
            error=str(exc),
        )

    # ── 3. Caddy preview routes ──
    if settings.caddy_admin_url and settings.caddy_preview_domain:
        try:
            transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
            manager = SubdomainManager(
                client=CaddyAdminClient(transport=transport),
                preview_domain=settings.caddy_preview_domain,
            )
            # Routes are id'd by workspace_slug (dev) or
            # `prod-<env_name>-<project_id>` (prod). The dev slugs
            # use `<workspace_id>-<label>` per service so the broadest
            # safe pattern is to fetch the full list and drop anything
            # whose id mentions the project_id or one of its workspace
            # ids.
            envs = (
                await db.execute(
                    select(models.Environment).where(
                        models.Environment.project_id == project_id,
                    )
                )
            ).scalars().all()
            slug_haystack = {project_id.lower()} | {
                w.id.lower() for w in workspaces
            } | {
                f"prod-{e.name}-{project_id}".lower() for e in envs
            }
            existing = await manager.list_routes()
            for route in existing:
                rid = route.get("@id") if isinstance(route, dict) else None
                if not isinstance(rid, str) or not rid.startswith("gapt-preview-"):
                    continue
                rid_lower = rid.lower()
                if any(needle in rid_lower for needle in slug_haystack):
                    try:
                        # `unregister(slug)` deletes the @id family for
                        # one slug; passing the slug embedded in @id is
                        # the easiest path that uses the manager's
                        # existing 404-tolerant unregister.
                        await manager.client.delete(f"/id/{rid}")
                        counts["caddy_routes"] += 1
                    except CaddyAdminError as exc:
                        if "404" not in str(exc):
                            logger.warning(
                                "project.archive.caddy_route_delete_failed",
                                project_id=project_id,
                                route_id=rid,
                                error=str(exc),
                            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "project.archive.caddy_cleanup_failed",
                project_id=project_id,
                error=str(exc),
            )

    return counts


@router.delete("/{project_id}", response_model=ProjectResponse)
async def archive_project(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: ProjectService = Depends(get_project_service),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> ProjectResponse:
    workspace_svc = _build_workspace_service_for_cleanup(container, settings)
    try:
        # Cascade BEFORE flipping `archived_at` so the workspaces
        # service's authorisation check (which gates on "project is
        # not archived") still passes.
        cleanup = await _cascade_archive_cleanup(
            db,
            actor=user,
            project_id=project_id,
            workspace_svc=workspace_svc,
            settings=settings,
        )
        view = await svc.archive(db, actor=user, project_id=project_id)
        await db.commit()
        logger.info(
            "project.archived",
            project_id=project_id,
            cascade=cleanup,
        )
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return ProjectResponse.from_view(view)


def _build_workspace_service_for_cleanup(
    container: AppContainer, settings: Settings
) -> WorkspaceService:
    """A minimal WorkspaceService for archive cascade. Reuses the
    container's sandbox backend + audit sink + workspace_sandbox
    manager. `credentials_resolver=None` is fine — delete() never
    starts a sandbox, only stops/destroys, so it doesn't need to
    inject secrets."""
    from gapt_server.domains.workspaces.service import (  # noqa: PLC0415
        WorkspaceService,
    )

    return WorkspaceService(
        sandbox_backend=container.sandbox_backend,
        sandbox_image=settings.sandbox_image_tag,
        audit_sink=container.audit_sink,
        session_factory=container.session_factory,
        credentials_resolver=None,
        workspace_sandbox=container.workspace_sandbox,
    )


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
