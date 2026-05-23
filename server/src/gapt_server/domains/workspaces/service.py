"""Workspace service — creates a sandbox + DB row in lockstep.

Lifecycle the M1 caller can ask for:

  create  → status = creating → sandbox.create → start → wait_for_daemon
            → status = running ; on failure → status = failed
  stop    → sandbox.stop                       → status = stopped
  start   → sandbox.start + wait_for_daemon    → status = running
  delete  → sandbox.destroy                    → status = archived

SeaweedFS volume mount + clone happen in Cycle 1.11 / M1-E2 — for now
the workspace just owns the sandbox handle and a `worktree_path` hint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import AuditAction, AuditEvent, AuditSink, NullAuditSink
from gapt_server.domains.projects.service import fetch_project_for
from gapt_server.domains.sandbox import (
    SandboxBackend,
    SandboxBackendError,
    SandboxCreateSpec,
    SandboxRef,
    SandboxResources,
    SecurityInvariantError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class WorkspaceError(RuntimeError):
    """Domain error — carries a stable code suffix."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceView:
    id: str
    project_id: str
    branch: str
    worktree_path: str
    sandbox_id: str | None
    status: enums.WorkspaceStatus
    last_activity_at: datetime
    created_at: datetime


def _view(row: models.Workspace) -> WorkspaceView:
    return WorkspaceView(
        id=row.id,
        project_id=row.project_id,
        branch=row.branch,
        worktree_path=row.worktree_path,
        sandbox_id=row.sandbox_id,
        status=row.status,
        last_activity_at=row.last_activity_at,
        created_at=row.created_at,
    )


class WorkspaceService:
    def __init__(
        self,
        *,
        sandbox_backend: SandboxBackend,
        sandbox_image: str,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._sandbox = sandbox_backend
        self._image = sandbox_image
        self._audit: AuditSink = audit_sink or NullAuditSink()

    # ────────────────────────────────────────────────────── create ──

    async def create(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        project_id: str,
        branch: str,
        worktree_path: str | None = None,
    ) -> WorkspaceView:
        project = await fetch_project_for(db, actor=actor, project_id=project_id)
        workspace_id = new_ulid()
        worktree = worktree_path or f"/workspace/{project.slug}/{workspace_id}"
        row = models.Workspace(
            id=workspace_id,
            project_id=project_id,
            branch=branch,
            worktree_path=worktree,
            status=enums.WorkspaceStatus.CREATING,
        )
        db.add(row)
        await db.flush()

        # Sandbox boot is best-effort; failure flips the row to FAILED
        # but keeps it around so the user can diagnose / retry.
        try:
            sandbox_ref = await self._boot_sandbox(project_id=project_id, workspace_id=workspace_id)
        except (SandboxBackendError, SecurityInvariantError) as exc:
            row.status = enums.WorkspaceStatus.FAILED
            await db.flush()
            await self._audit.log(
                AuditEvent(
                    action=AuditAction.WORKSPACE_CREATE,
                    actor_type=enums.AuditActorType.USER,
                    actor_id=actor.id,
                    outcome=enums.AuditOutcome.ERROR,
                    scope={"project_id": project_id, "workspace_id": workspace_id},
                    payload={"error": str(exc)},
                )
            )
            raise WorkspaceError(
                "workspace.sandbox_boot_failed", f"sandbox boot failed: {exc}"
            ) from exc

        sandbox_row = models.Sandbox(
            id=sandbox_ref.id,
            project_id=project_id,
            workspace_id=workspace_id,
            status=enums.SandboxStatus.RUNNING,
            container_id=sandbox_ref.container_id,
            image_tag=self._image,
        )
        db.add(sandbox_row)
        row.sandbox_id = sandbox_ref.id
        row.status = enums.WorkspaceStatus.RUNNING
        row.last_activity_at = _now()
        await db.flush()

        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_CREATE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": project_id, "workspace_id": workspace_id},
                subject={"branch": branch, "worktree_path": worktree},
                payload={"sandbox_id": sandbox_ref.id, "image": self._image},
            )
        )
        return _view(row)

    async def _boot_sandbox(self, *, project_id: str, workspace_id: str) -> SandboxRef:
        spec = SandboxCreateSpec(
            project_id=project_id,
            workspace_id=workspace_id,
            image=self._image,
            resources=SandboxResources(),
        )
        ref = await self._sandbox.create(spec)
        await self._sandbox.start(ref)
        return ref

    # ───────────────────────────────────────────────────── read ──

    async def list_for_project(
        self, db: AsyncSession, *, actor: models.User, project_id: str
    ) -> list[WorkspaceView]:
        await fetch_project_for(db, actor=actor, project_id=project_id)
        rows = (
            (
                await db.execute(
                    select(models.Workspace)
                    .where(models.Workspace.project_id == project_id)
                    .order_by(models.Workspace.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_view(r) for r in rows]

    async def get(
        self, db: AsyncSession, *, actor: models.User, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(db, actor=actor, workspace_id=workspace_id)
        return _view(row)

    # ──────────────────────────────────────────────────── mutate ──

    async def stop(
        self, db: AsyncSession, *, actor: models.User, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id, min_role=enums.Role.EDITOR
        )
        await self._sandbox_for(row, action="stop")
        row.status = enums.WorkspaceStatus.STOPPED
        row.last_activity_at = _now()
        await db.flush()
        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_STOP,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": row.project_id, "workspace_id": workspace_id},
            )
        )
        return _view(row)

    async def start(
        self, db: AsyncSession, *, actor: models.User, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id, min_role=enums.Role.EDITOR
        )
        await self._sandbox_for(row, action="start")
        row.status = enums.WorkspaceStatus.RUNNING
        row.last_activity_at = _now()
        await db.flush()
        return _view(row)

    async def delete(
        self, db: AsyncSession, *, actor: models.User, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id, min_role=enums.Role.ADMIN
        )
        await self._sandbox_for(row, action="destroy", swallow_missing=True)
        row.status = enums.WorkspaceStatus.ARCHIVED
        row.last_activity_at = _now()
        await db.flush()
        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_DELETE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": row.project_id, "workspace_id": workspace_id},
            )
        )
        return _view(row)

    # ──────────────────────────────────────────────── internals ──

    async def _sandbox_for(
        self,
        row: models.Workspace,
        *,
        action: str,
        swallow_missing: bool = False,
    ) -> None:
        if row.sandbox_id is None:
            if swallow_missing:
                return
            raise WorkspaceError(
                "workspace.no_sandbox", f"workspace {row.id} has no attached sandbox"
            )
        ref = SandboxRef(id=row.sandbox_id, container_id=None, backend=self._sandbox.name)
        try:
            if action == "start":
                await self._sandbox.start(ref)
            elif action == "stop":
                await self._sandbox.stop(ref)
            elif action == "destroy":
                await self._sandbox.destroy(ref)
            else:  # pragma: no cover — defensive
                raise WorkspaceError("workspace.bad_action", f"unknown action {action!r}")
        except SandboxBackendError as exc:
            if swallow_missing:
                logger.warning(
                    "workspace.sandbox_action_swallowed",
                    workspace_id=row.id,
                    action=action,
                    error=str(exc),
                )
                return
            raise WorkspaceError(
                "workspace.sandbox_action_failed",
                f"sandbox {action} failed: {exc}",
            ) from exc

    async def _fetch(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        workspace_id: str,
        min_role: enums.Role = enums.Role.VIEWER,
    ) -> models.Workspace:
        row = (
            await db.execute(select(models.Workspace).where(models.Workspace.id == workspace_id))
        ).scalar_one_or_none()
        if row is None:
            raise WorkspaceError("workspace.not_found", f"workspace_id={workspace_id}")

        # Reuse the project authorisation gate.
        await fetch_project_for(db, actor=actor, project_id=row.project_id, min_role=min_role)
        return row


def _now() -> datetime:
    return datetime.now(tz=UTC)
