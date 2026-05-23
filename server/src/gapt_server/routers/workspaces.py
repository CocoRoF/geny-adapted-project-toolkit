"""Workspace lifecycle endpoints.

- `POST   /api/projects/{pid}/workspaces`  — create (boots sandbox)
- `GET    /api/projects/{pid}/workspaces`  — list (member only)
- `GET    /api/workspaces/{wid}`           — fetch
- `POST   /api/workspaces/{wid}/stop`      — stop sandbox (≥ editor)
- `POST   /api/workspaces/{wid}/start`     — restart sandbox (≥ editor)
- `DELETE /api/workspaces/{wid}`           — archive + tear down (≥ admin)
- `GET    /api/workspaces/{wid}/tree`      — list directory contents
- `GET    /api/workspaces/{wid}/file`      — read file contents
- `PUT    /api/workspaces/{wid}/file`      — write/overwrite a file
- `DELETE /api/workspaces/{wid}/file`      — delete a file or empty dir
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
from gapt_server.db import enums, models
from gapt_server.domains.audit.sink import AuditSink  # noqa: TC001
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.domains.sandbox import (
    SandboxBackend,
    SandboxRef,
)
from gapt_server.domains.workspaces import files as fs
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

from sqlalchemy import select


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


# ───────────────────────────────────────────── workspace file API ──


class TreeEntryResponse(BaseModel):
    name: str
    path: str
    kind: str  # "file" | "dir"
    size: int | None = None


class FileContentResponse(BaseModel):
    path: str
    encoding: str
    text: str


class WriteFileRequest(BaseModel):
    content: str = Field(default="", max_length=2_000_000)
    encoding: str = Field(default="utf-8", pattern="^(utf-8|base64)$")


def _http_from_fs_error(exc: fs.WorkspaceFileError) -> HTTPException:
    code = exc.code
    if code in {"workspace.path.invalid", "workspace.path.traversal"}:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": code, "reason": str(exc)},
        )
    if code == "workspace.fs.not_found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": code, "reason": str(exc)},
        )
    if code == "workspace.fs.too_large":
        return HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": code, "reason": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": code, "reason": str(exc)},
    )


async def _workspace_for_fs(
    db: AsyncSession,
    *,
    user: models.User,
    workspace_id: str,
) -> tuple[models.Workspace, SandboxRef]:
    row = (
        await db.execute(select(models.Workspace).where(models.Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace.not_found", "reason": workspace_id},
        )
    try:
        await fetch_project_for(db, actor=user, project_id=row.project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc
    if row.sandbox_id is None or row.status != enums.WorkspaceStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "workspace.fs.not_running",
                "reason": f"workspace {workspace_id} is {row.status.value} — start it first",
            },
        )
    ref = SandboxRef(id=row.sandbox_id, container_id=None, backend="mock")
    return row, ref


@by_id.get("/{workspace_id}/tree", response_model=list[TreeEntryResponse])
async def tree(
    workspace_id: str,
    path: str = "/",
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    sandbox: SandboxBackend = Depends(get_sandbox_backend),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> list[TreeEntryResponse]:
    workspace, ref = await _workspace_for_fs(db, user=user, workspace_id=workspace_id)
    try:
        entries = await fs.list_tree(sandbox, ref, worktree_path=workspace.worktree_path, path=path)
    except fs.WorkspaceFileError as exc:
        raise _http_from_fs_error(exc) from exc
    return [TreeEntryResponse(**vars(e)) for e in entries]


@by_id.get("/{workspace_id}/file", response_model=FileContentResponse)
async def read_workspace_file(
    workspace_id: str,
    path: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    sandbox: SandboxBackend = Depends(get_sandbox_backend),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> FileContentResponse:
    workspace, ref = await _workspace_for_fs(db, user=user, workspace_id=workspace_id)
    try:
        content = await fs.read_file(sandbox, ref, worktree_path=workspace.worktree_path, path=path)
    except fs.WorkspaceFileError as exc:
        raise _http_from_fs_error(exc) from exc
    return FileContentResponse(**vars(content))


@by_id.put("/{workspace_id}/file", response_model=FileContentResponse)
async def write_workspace_file(
    workspace_id: str,
    path: str,
    payload: WriteFileRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    sandbox: SandboxBackend = Depends(get_sandbox_backend),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> FileContentResponse:
    workspace, ref = await _workspace_for_fs(db, user=user, workspace_id=workspace_id)
    try:
        await fs.write_file(
            sandbox,
            ref,
            worktree_path=workspace.worktree_path,
            path=path,
            content=payload.content,
            encoding=payload.encoding,
        )
    except fs.WorkspaceFileError as exc:
        raise _http_from_fs_error(exc) from exc
    return FileContentResponse(path=path, encoding=payload.encoding, text=payload.content)


@by_id.delete(
    "/{workspace_id}/file",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_path(
    workspace_id: str,
    path: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    sandbox: SandboxBackend = Depends(get_sandbox_backend),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> None:
    workspace, ref = await _workspace_for_fs(db, user=user, workspace_id=workspace_id)
    try:
        await fs.delete_path(sandbox, ref, worktree_path=workspace.worktree_path, path=path)
    except fs.WorkspaceFileError as exc:
        raise _http_from_fs_error(exc) from exc
