"""Workspace lifecycle endpoints.

- `POST   /api/projects/{pid}/workspaces`  — create (boots sandbox)
- `GET    /api/projects/{pid}/workspaces`  — list (member only)
- `GET    /api/workspaces/{wid}`           — fetch
- `POST   /api/workspaces/{wid}/stop`      — stop sandbox (≥ editor)
- `POST   /api/workspaces/{wid}/start`     — restart sandbox (≥ editor)
- `DELETE /api/workspaces/{wid}`           — archive + tear down (≥ admin)
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  — pydantic runtime introspection
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from gapt_server.container import (
    get_app_settings,
    get_audit_sink,
    get_db_session,
    get_sandbox_backend,
)
from gapt_server.db import enums, models  # noqa: TC001
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001
from gapt_server.domains.projects.service import ProjectError
from gapt_server.domains.sandbox import SandboxBackend  # noqa: TC001
from gapt_server.domains.workspaces.service import (
    WorkspaceError,
    WorkspaceService,
    WorkspaceView,
)
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.settings import Settings


def get_workspace_service(
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    sandbox: SandboxBackend = Depends(get_sandbox_backend),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> WorkspaceService:
    return WorkspaceService(
        sandbox_backend=sandbox,
        sandbox_image=settings.sandbox_image_tag,
        audit_sink=audit_sink,
    )


by_project = APIRouter(prefix="/api/projects", tags=["workspaces"])
by_id = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


# ──────────────────────────────────────────────────────── DTOs ──


class CreateWorkspaceRequest(BaseModel):
    branch: str = Field(min_length=1, max_length=255)
    worktree_path: str | None = Field(default=None, min_length=1, max_length=4096)


class WorkspaceResponse(BaseModel):
    id: str
    project_id: str
    branch: str
    worktree_path: str
    sandbox_id: str | None
    status: enums.WorkspaceStatus
    last_activity_at: datetime
    created_at: datetime

    @classmethod
    def from_view(cls, v: WorkspaceView) -> WorkspaceResponse:
        return cls(**v.__dict__)


def _http_from_workspace_error(exc: WorkspaceError) -> HTTPException:
    if exc.code == "workspace.not_found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "reason": str(exc)},
        )
    if exc.code in {
        "workspace.sandbox_boot_failed",
        "workspace.sandbox_action_failed",
        "workspace.no_sandbox",
    }:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "reason": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": exc.code, "reason": str(exc)},
    )


# ────────────────────────────────────────────────── endpoints ──


@by_project.post(
    "/{project_id}/workspaces",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    project_id: str,
    payload: CreateWorkspaceRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> WorkspaceResponse:
    try:
        view = await svc.create(
            db,
            actor=user,
            project_id=project_id,
            branch=payload.branch,
            worktree_path=payload.worktree_path,
        )
        await db.commit()
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    except WorkspaceError as exc:
        # The sandbox failure path already flipped the row to FAILED and
        # committed an audit row — surface a 409 so the caller can
        # diagnose without us swallowing the cause.
        await db.commit()
        raise _http_from_workspace_error(exc) from exc
    return WorkspaceResponse.from_view(view)


@by_project.get("/{project_id}/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> list[WorkspaceResponse]:
    try:
        views = await svc.list_for_project(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return [WorkspaceResponse.from_view(v) for v in views]


@by_id.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> WorkspaceResponse:
    try:
        view = await svc.get(db, actor=user, workspace_id=workspace_id)
    except WorkspaceError as exc:
        raise _http_from_workspace_error(exc) from exc
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return WorkspaceResponse.from_view(view)


@by_id.post("/{workspace_id}/stop", response_model=WorkspaceResponse)
async def stop_workspace(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> WorkspaceResponse:
    try:
        view = await svc.stop(db, actor=user, workspace_id=workspace_id)
        await db.commit()
    except WorkspaceError as exc:
        raise _http_from_workspace_error(exc) from exc
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return WorkspaceResponse.from_view(view)


@by_id.post("/{workspace_id}/start", response_model=WorkspaceResponse)
async def start_workspace(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> WorkspaceResponse:
    try:
        view = await svc.start(db, actor=user, workspace_id=workspace_id)
        await db.commit()
    except WorkspaceError as exc:
        raise _http_from_workspace_error(exc) from exc
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return WorkspaceResponse.from_view(view)


@by_id.delete("/{workspace_id}", response_model=WorkspaceResponse)
async def delete_workspace(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    svc: WorkspaceService = Depends(get_workspace_service),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> WorkspaceResponse:
    try:
        view = await svc.delete(db, actor=user, workspace_id=workspace_id)
        await db.commit()
    except WorkspaceError as exc:
        raise _http_from_workspace_error(exc) from exc
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    return WorkspaceResponse.from_view(view)
