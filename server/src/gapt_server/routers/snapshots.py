"""Workspace snapshots — git-grade, AI-first checkpoints.

    POST   /_gapt/api/workspaces/{wid}/snapshots          capture
    GET    /_gapt/api/workspaces/{wid}/snapshots          list (newest first)
    GET    /_gapt/api/snapshots/{id}                       fetch (+ activity)
    GET    /_gapt/api/snapshots/{id}/diff                  unified diff vs parent
    GET    /_gapt/api/snapshots/{id}/activity             chat + tool transcript
    POST   /_gapt/api/snapshots/{id}/restore              reset a workspace to it
    DELETE /_gapt/api/snapshots/{id}                       remove (ref + row)

Capture/restore/diff run git inside the workspace container; reads come from the
``snapshots`` rows. See ``domains/snapshots/service.py`` for the mechanics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from gapt_server.container import (
    get_audit_sink,
    get_container,
    get_db_session,
)
from gapt_server.db import enums, models
from gapt_server.domains import snapshots as snap_svc
from gapt_server.domains.audit.sink import AuditEvent, AuditSink  # noqa: TC001
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.container import AppContainer
    from gapt_server.domains.workspace_sandbox import WorkspaceSandbox

by_workspace = APIRouter(prefix="/_gapt/api/workspaces", tags=["snapshots"])
by_id = APIRouter(prefix="/_gapt/api/snapshots", tags=["snapshots"])


# ── schemas ─────────────────────────────────────────────────────────────


class CreateSnapshotRequest(BaseModel):
    label: str = ""
    # When set, the snapshot pins this session's recent activity (chat + tool
    # calls). Geny passes it for ``tool_save`` snapshots.
    session_id: str | None = None
    kind: str = "manual"  # "manual" | "tool_save"
    # None → auto (True for tool_save so build artifacts are captured).
    include_ignored: bool | None = None


class RestoreSnapshotRequest(BaseModel):
    # Restore into this workspace (default = the snapshot's own workspace).
    target_workspace_id: str | None = None
    clean: bool = True


class SnapshotResponse(BaseModel):
    id: str
    workspace_id: str
    session_id: str | None
    parent_id: str | None
    kind: str
    label: str
    git_sha: str
    git_ref: str
    event_start_seq: int | None
    event_end_seq: int | None
    stats: dict[str, Any]
    created_at: str
    created_by: str | None

    @classmethod
    def of(cls, s: models.Snapshot) -> "SnapshotResponse":
        return cls(
            id=s.id,
            workspace_id=s.workspace_id,
            session_id=s.session_id,
            parent_id=s.parent_id,
            kind=s.kind.value if isinstance(s.kind, enums.SnapshotKind) else str(s.kind),
            label=s.label,
            git_sha=s.git_sha,
            git_ref=s.git_ref,
            event_start_seq=s.event_start_seq,
            event_end_seq=s.event_end_seq,
            stats=s.stats or {},
            created_at=s.created_at.isoformat() if s.created_at else "",
            created_by=s.created_by,
        )


# ── helpers ─────────────────────────────────────────────────────────────


async def _resolve_workspace(
    db: "AsyncSession", *, workspace_id: str, require_running: bool
) -> models.Workspace:
    ws = (
        await db.execute(
            select(models.Workspace).where(models.Workspace.id == workspace_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workspace.not_found", "reason": workspace_id},
        )
    if require_running and ws.status != enums.WorkspaceStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "workspace.not_running", "reason": ws.status.value},
        )
    return ws


async def _sandbox_for(
    container: "AppContainer", ws: models.Workspace
) -> "WorkspaceSandbox":
    sandbox = container.workspace_sandbox.get(ws.id, ws.worktree_path)
    await sandbox.ensure()
    return sandbox


async def _resolve_snapshot(db: "AsyncSession", snapshot_id: str) -> models.Snapshot:
    snap = await snap_svc.get(db, snapshot_id=snapshot_id)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "snapshot.not_found", "reason": snapshot_id},
        )
    return snap


def _parse_kind(raw: str) -> enums.SnapshotKind:
    try:
        return enums.SnapshotKind(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "snapshot.bad_kind", "reason": raw},
        ) from None


async def _audit(
    sink: AuditSink, *, action: str, actor: str | None, outcome: enums.AuditOutcome, **scope: Any
) -> None:
    try:
        await sink.log(
            AuditEvent(
                action=action,
                actor_type=enums.AuditActorType.USER,
                outcome=outcome,
                actor_id=actor,
                scope=scope,
            )
        )
    except Exception:  # noqa: BLE001 — audit never blocks the operation
        pass


# ── endpoints ───────────────────────────────────────────────────────────


@by_workspace.post("/{workspace_id}/snapshots", response_model=SnapshotResponse)
async def create_snapshot(
    workspace_id: str,
    payload: CreateSnapshotRequest,
    db: "AsyncSession" = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: "AppContainer" = Depends(get_container),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> SnapshotResponse:
    ws = await _resolve_workspace(db, workspace_id=workspace_id, require_running=True)
    kind = _parse_kind(payload.kind)
    sandbox = await _sandbox_for(container, ws)
    try:
        snap = await snap_svc.capture(
            db,
            sandbox=sandbox,
            workspace=ws,
            session_id=payload.session_id,
            kind=kind,
            label=payload.label,
            include_ignored=payload.include_ignored,
            created_by=user.id,
        )
    except snap_svc.SnapshotError as exc:
        await _audit(audit_sink, action="snapshot.create", actor=user.id,
                     outcome=enums.AuditOutcome.ERROR, workspace_id=workspace_id, code=exc.code)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    await db.commit()  # get_db_session does not auto-commit; persist the row
    await _audit(audit_sink, action="snapshot.create", actor=user.id,
                 outcome=enums.AuditOutcome.OK, workspace_id=workspace_id, snapshot_id=snap.id)
    return SnapshotResponse.of(snap)


@by_workspace.get("/{workspace_id}/snapshots", response_model=list[SnapshotResponse])
async def list_snapshots(
    workspace_id: str,
    db: "AsyncSession" = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[SnapshotResponse]:
    _ = user
    await _resolve_workspace(db, workspace_id=workspace_id, require_running=False)
    rows = await snap_svc.list_for_workspace(db, workspace_id=workspace_id)
    return [SnapshotResponse.of(s) for s in rows]


@by_id.get("/{snapshot_id}")
async def get_snapshot(
    snapshot_id: str,
    db: "AsyncSession" = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    _ = user
    snap = await _resolve_snapshot(db, snapshot_id)
    out = SnapshotResponse.of(snap).model_dump()
    out["activity"] = snap.activity or {}
    return out


@by_id.get("/{snapshot_id}/activity")
async def get_snapshot_activity(
    snapshot_id: str,
    db: "AsyncSession" = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    _ = user
    snap = await _resolve_snapshot(db, snapshot_id)
    return {
        "snapshot_id": snap.id,
        "event_start_seq": snap.event_start_seq,
        "event_end_seq": snap.event_end_seq,
        "activity": snap.activity or {},
    }


@by_id.get("/{snapshot_id}/diff")
async def get_snapshot_diff(
    snapshot_id: str,
    db: "AsyncSession" = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: "AppContainer" = Depends(get_container),  # noqa: B008
) -> dict[str, Any]:
    _ = user
    snap = await _resolve_snapshot(db, snapshot_id)
    ws = await _resolve_workspace(db, workspace_id=snap.workspace_id, require_running=True)
    sandbox = await _sandbox_for(container, ws)
    return await snap_svc.compute_diff(sandbox=sandbox, snapshot=snap)


@by_id.post("/{snapshot_id}/restore")
async def restore_snapshot(
    snapshot_id: str,
    payload: RestoreSnapshotRequest,
    db: "AsyncSession" = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: "AppContainer" = Depends(get_container),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> dict[str, Any]:
    snap = await _resolve_snapshot(db, snapshot_id)
    target_id = payload.target_workspace_id or snap.workspace_id
    ws = await _resolve_workspace(db, workspace_id=target_id, require_running=True)
    sandbox = await _sandbox_for(container, ws)
    try:
        result = await snap_svc.restore(db, sandbox=sandbox, snapshot=snap, clean=payload.clean)
    except snap_svc.SnapshotError as exc:
        await _audit(audit_sink, action="snapshot.restore", actor=user.id,
                     outcome=enums.AuditOutcome.ERROR, snapshot_id=snap.id, code=exc.code)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "reason": str(exc)},
        ) from exc
    await _audit(audit_sink, action="snapshot.restore", actor=user.id,
                 outcome=enums.AuditOutcome.OK, snapshot_id=snap.id, target_workspace_id=target_id)
    return {"ok": True, "snapshot_id": snap.id, "target_workspace_id": target_id, **result}


@by_id.delete("/{snapshot_id}")
async def delete_snapshot(
    snapshot_id: str,
    db: "AsyncSession" = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    container: "AppContainer" = Depends(get_container),  # noqa: B008
    audit_sink: AuditSink = Depends(get_audit_sink),  # noqa: B008
) -> dict[str, Any]:
    snap = await _resolve_snapshot(db, snapshot_id)
    # Best-effort ref cleanup needs a live sandbox; if the workspace is gone or
    # stopped we still drop the row (the commit object is GC'd by git later).
    sandbox: "WorkspaceSandbox | None" = None
    ws = (
        await db.execute(
            select(models.Workspace).where(models.Workspace.id == snap.workspace_id)
        )
    ).scalar_one_or_none()
    if ws is not None and ws.status == enums.WorkspaceStatus.RUNNING:
        try:
            sandbox = await _sandbox_for(container, ws)
        except Exception:  # noqa: BLE001
            sandbox = None
    await snap_svc.delete(db, sandbox=sandbox, snapshot=snap)
    await db.commit()  # persist the row removal
    await _audit(audit_sink, action="snapshot.delete", actor=user.id,
                 outcome=enums.AuditOutcome.OK, snapshot_id=snapshot_id)
    return {"ok": True, "snapshot_id": snapshot_id}
