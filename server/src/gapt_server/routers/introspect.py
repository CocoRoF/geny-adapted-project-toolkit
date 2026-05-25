"""Introspection — read a workspace's worktree, suggest dev + prod
config + env file layout.

Two endpoints:

  `GET  /api/workspaces/{wid}/introspect`         — what we'd suggest
  `POST /api/workspaces/{wid}/apply-introspection` — actually create
                                                     the rows

The first-open wizard in the IDE calls GET right after the
workspace finishes cloning to show the user a preview. When they
click "Use these settings" the wizard POSTs apply-introspection and
the backend materialises a dev Service + prod Environment from the
suggestion. Subsequent calls are upsert-safe.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from gapt_server.container import (
    get_app_settings,
    get_db_session,
    get_service_registry,
)
from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from pathlib import Path

from gapt_server.domains.introspection import (
    ProjectIntrospection,
    detect,
    patch_nextjs_basepath,
)
from gapt_server.domains.services import ServiceRegistry, ServiceAlreadyExists
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.settings import Settings


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
    install_command: str | None = None
    test_command: str | None = None
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
    return _to_response(result)


def _to_response(result: ProjectIntrospection) -> IntrospectResponse:
    return IntrospectResponse(
        kind=result.kind.value,
        has_compose=result.has_compose,
        secondary_stacks=result.secondary_stacks,
        dev_command=result.dev_command,
        dev_port=result.dev_port,
        dev_cwd=result.dev_cwd,
        dev_env_hints=result.dev_env_hints,
        install_command=result.install_command,
        test_command=result.test_command,
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


# ─────────────────────────────────────────────────── apply ──


class ApplyIntrospectionRequest(BaseModel):
    """Optional fine-grained overrides. The wizard either sends `{}`
    to accept everything the detector found, or specific fields to
    override before materialising the rows. Missing field = use
    detector value."""

    model_config = ConfigDict(populate_by_name=True)

    # Per-resource opt-in toggles — UI can let the user uncheck "no
    # thanks, don't make the dev Service" without losing the prod env
    # creation in the same call.
    create_dev_service: bool = True
    create_prod_environment: bool = True

    # Overrides for the dev side.
    dev_label: str = "dev"
    dev_command: str | None = None
    dev_port: int | None = None
    dev_cwd: str | None = None
    # When True, prepend `<install_command> && ` to the dev cmd so
    # the dev service installs deps before starting. Idempotent: pip
    # / npm / pnpm all skip "already-installed" packages fast. Set
    # to False if you've already installed manually.
    dev_run_install: bool = True

    # Overrides for the prod side.
    prod_environment_name: str = "prod"
    prod_compose_path: str | None = None
    prod_compose_paths: list[str] | None = None
    prod_primary_service: str | None = None
    prod_primary_port: int | None = None
    prod_build: bool | None = None  # when None, take detector's `prod_build_required`
    prod_preview_mode: str = "path"  # "path" | "subdomain"


class ApplyIntrospectionResponse(BaseModel):
    introspection: IntrospectResponse
    created_dev_service: dict[str, Any] | None = Field(default=None)
    created_environment: dict[str, Any] | None = Field(default=None)
    # Human-readable summary of what changed — UI shows as a toast.
    actions: list[str] = Field(default_factory=list)


@router.post(
    "/{workspace_id}/apply-introspection",
    response_model=ApplyIntrospectionResponse,
)
async def apply_introspection(
    workspace_id: str,
    payload: ApplyIntrospectionRequest,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
    registry: ServiceRegistry = Depends(get_service_registry),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> ApplyIntrospectionResponse:
    """Materialise the introspection result into real rows.

    Idempotent:
      * dev Service: keyed by (workspace_id, label). If one already
        exists with the same label we return it unchanged (the user
        can `restart` to pick up new command/port).
      * prod Environment: keyed by (project_id, name). Existing row
        is patched with the detector's compose_path / primary_service
        / primary_port; secret_refs + require_2fa preserved.
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
                "reason": f"workspace is {ws.status.value}",
            },
        )

    intro = detect(ws.worktree_path)
    actions: list[str] = []

    # ─── dev Service ───
    created_dev: dict[str, Any] | None = None
    if payload.create_dev_service:
        dev_cmd = payload.dev_command or intro.dev_command
        dev_port = payload.dev_port or intro.dev_port
        dev_cwd = payload.dev_cwd if payload.dev_cwd is not None else intro.dev_cwd
        if dev_cmd:
            # Two transforms before this command lands in the
            # ServiceRegistry:
            #   1. Prepend `<install_command> && ` so the first dev
            #      start doesn't `command not found` because deps
            #      haven't been pulled. Idempotent on rerun.
            #   2. Wrap with `cd <dev_cwd> &&` when the project is a
            #      monorepo (`frontend/src`) — sandbox's
            #      spawn_background always cwd=/workspace.
            cmd_to_run = dev_cmd
            if payload.dev_run_install and intro.install_command:
                cmd_to_run = f"{intro.install_command} && {dev_cmd}"
            if dev_cwd:
                cmd_to_run = f"cd {dev_cwd} && {cmd_to_run}"
            try:
                svc = await registry.start(
                    workspace_id=workspace_id,
                    label=payload.dev_label,
                    cmd=cmd_to_run,
                    worktree_path=ws.worktree_path,
                    port=dev_port,
                )
                created_dev = svc.snapshot()
                actions.append(
                    f"started dev service {payload.dev_label!r} → `{cmd_to_run}`"
                )
            except ServiceAlreadyExists:
                # Already running — surface the existing row.
                try:
                    svc = await registry.get(workspace_id, payload.dev_label)
                    created_dev = svc.snapshot()
                    actions.append(
                        f"dev service {payload.dev_label!r} already running — kept"
                    )
                except Exception:  # noqa: BLE001
                    pass
            except RuntimeError as exc:
                actions.append(f"dev service spawn failed: {exc}")
        else:
            actions.append("no dev command detected — skipped dev service")

    # ─── prod Environment ───
    created_env: dict[str, Any] | None = None
    if payload.create_prod_environment:
        # Resolve the compose paths into absolute paths so docker
        # compose (run from server cwd) reads the right files.
        worktree = ws.worktree_path
        rel_path = payload.prod_compose_path or intro.prod_compose_path
        rel_paths = (
            payload.prod_compose_paths
            if payload.prod_compose_paths is not None
            else intro.prod_compose_paths
        )
        if rel_path or rel_paths:
            abs_path = (
                os.path.join(worktree, rel_path) if rel_path else "docker-compose.yml"
            )
            abs_paths = [os.path.join(worktree, p) for p in rel_paths]
            primary_service = (
                payload.prod_primary_service or intro.prod_primary_service
            )
            primary_port = payload.prod_primary_port or intro.prod_primary_port
            build = (
                payload.prod_build
                if payload.prod_build is not None
                else intro.prod_build_required
            )
            cfg: dict[str, Any] = {
                "compose_path": abs_path,
                "compose_paths": abs_paths,
                "build": bool(build),
                "preview_mode": payload.prod_preview_mode,
            }
            if primary_service:
                cfg["primary_service"] = primary_service
            if primary_port:
                cfg["primary_port"] = primary_port

            # Upsert by (project_id, name).
            existing = (
                await db.execute(
                    select(models.Environment).where(
                        (models.Environment.project_id == ws.project_id)
                        & (models.Environment.name == payload.prod_environment_name)
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.deploy_target_config = {
                    **(existing.deploy_target_config or {}),
                    **cfg,
                }
                env_row = existing
                actions.append(
                    f"updated environment {payload.prod_environment_name!r} with detected compose config"
                )
            else:
                env_row = models.Environment(
                    id=new_ulid(),
                    project_id=ws.project_id,
                    name=payload.prod_environment_name,
                    deploy_target_kind=enums.DeployTargetKind.LOCAL,
                    deploy_target_config=cfg,
                    require_2fa=False,
                    secret_refs=[],
                    cost_multiplier=1.0,
                    hooks={},
                    last_run={},
                )
                db.add(env_row)
                actions.append(
                    f"created environment {payload.prod_environment_name!r} → "
                    f"{primary_service or '?'}:{primary_port or '?'}"
                )
            await db.flush()
            await db.commit()
            created_env = {
                "id": env_row.id,
                "name": env_row.name,
                "deploy_target_kind": env_row.deploy_target_kind.value,
                "deploy_target_config": dict(env_row.deploy_target_config),
            }
        else:
            actions.append("no compose file detected — skipped prod environment")

    # `settings` is captured for symmetry with the deploy router and
    # to reserve a slot for future per-tenant defaults; nothing reads
    # it yet but mypy treats unused fixtures as errors in strict mode.
    _ = settings

    return ApplyIntrospectionResponse(
        introspection=_to_response(intro),
        created_dev_service=created_dev,
        created_environment=created_env,
        actions=actions,
    )


# ────────────────────────────────────── auto-patch (F1.5) ──


class AutoPatchResponse(BaseModel):
    """Mirror of `PatchResult` — the wizard shows this as a
    checklist + a "what to do next" hint."""

    patched_files: list[str]
    skipped: list[str]
    next_steps: list[str]


@router.post(
    "/{workspace_id}/auto-patch/nextjs-basepath",
    response_model=AutoPatchResponse,
)
async def auto_patch_nextjs_basepath(
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> AutoPatchResponse:
    """Patch the workspace clone's Next.js config + Dockerfile so
    the app builds with the right `basePath`. Only touches files
    inside the workspace clone — the user's GitHub repo stays as-is.
    Idempotent.

    Pre-conditions:
      * workspace is `running`
      * introspection says `needs_basepath` is true
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
            detail={"code": "workspace.not_running", "reason": ws.status.value},
        )

    intro = detect(ws.worktree_path)
    if not intro.needs_basepath:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "introspect.basepath_not_applicable",
                "reason": "introspection did not flag this project as basePath-capable",
            },
        )

    result = patch_nextjs_basepath(
        worktree=Path(ws.worktree_path),
        next_config_path=intro.basepath_config_file,
        frontend_dockerfile_path=None,
    )
    return AutoPatchResponse(
        patched_files=result.patched_files,
        skipped=result.skipped,
        next_steps=result.next_steps,
    )
