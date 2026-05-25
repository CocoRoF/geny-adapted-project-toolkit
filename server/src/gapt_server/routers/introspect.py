"""Introspection — read a workspace's worktree, suggest dev + prod
config + env file layout.

One read-only endpoint:

  `GET /api/workspaces/{workspace_id}/introspect`

The first-open wizard in the IDE calls this right after the
workspace finishes cloning, then either pre-populates the new env
form with the suggestion or auto-applies it (when confidence ≥ 0.8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import get_db_session
from gapt_server.db import enums, models
from gapt_server.domains.introspection import detect
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


router = APIRouter(prefix="/api/workspaces", tags=["introspect"])


class IntrospectResponse(BaseModel):
    """Mirror of `ProjectIntrospection` for the wire. We keep field
    names identical so the web tier can consume it without
    translation."""

    kind: str
    has_compose: bool
    secondary_stacks: list[str] = Field(default_factory=list)
    dev_command: str | None = None
    dev_port: int | None = None
    dev_cwd: str | None = None
    dev_env_hints: dict[str, str] = Field(default_factory=dict)
    prod_compose_path: str | None = None
    prod_compose_paths: list[str] = Field(default_factory=list)
    prod_primary_service: str | None = None
    prod_primary_port: int | None = None
    prod_build_required: bool = False
    env_files: list[str] = Field(default_factory=list)
    env_examples: list[str] = Field(default_factory=list)
    needs_basepath: bool = False
    basepath_config_file: str | None = None
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


@router.get(
    "/{workspace_id}/introspect", response_model=IntrospectResponse
)
async def introspect_workspace(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> IntrospectResponse:
    """Run detectors against the workspace's worktree.

    The workspace must be in `running` state — detecting against a
    half-cloned tree gives wrong answers. We bounce `creating` /
    `failed` / `archived` workspaces with the same conflict shape
    the terminal/services endpoints use.
    """
    ws = (
        await db.execute(select(models.Workspace).where(models.Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace.not_found", "reason": workspace_id},
        )
    membership = (
        await db.execute(
            select(models.ProjectMembership).where(
                (models.ProjectMembership.project_id == ws.project_id)
                & (models.ProjectMembership.user_id == user.id)
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "workspace.forbidden", "reason": workspace_id},
        )
    if ws.status != enums.WorkspaceStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "workspace.not_running",
                "reason": f"workspace is {ws.status.value} — wait for clone to finish",
            },
        )

    result = detect(ws.worktree_path)
    return IntrospectResponse(
        kind=result.kind.value,
        has_compose=result.has_compose,
        secondary_stacks=result.secondary_stacks,
        dev_command=result.dev_command,
        dev_port=result.dev_port,
        dev_cwd=result.dev_cwd,
        dev_env_hints=result.dev_env_hints,
        prod_compose_path=result.prod_compose_path,
        prod_compose_paths=result.prod_compose_paths,
        prod_primary_service=result.prod_primary_service,
        prod_primary_port=result.prod_primary_port,
        prod_build_required=result.prod_build_required,
        env_files=result.env_files,
        env_examples=result.env_examples,
        needs_basepath=result.needs_basepath,
        basepath_config_file=result.basepath_config_file,
        confidence=result.confidence,
        notes=result.notes,
        sources=result.sources,
    )
