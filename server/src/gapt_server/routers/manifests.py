"""`/_gapt/api/manifests` — list bundled + workspace-local agent
manifests.

The chat panel's manifest picker (Phase G.3) reads this; the
session-create flow's `env_id` field is the slug returned in `id`.

Surface:

    GET /_gapt/api/manifests
        Returns every manifest visible to the current admin:
        - server-bundled (under `gapt_server/manifests/*.json`)
        - workspace-local (under `<workspace>/.gapt/manifests/*.json`)
          when `workspace_id` query param is supplied.

    GET /_gapt/api/manifests/{id}
        Single-manifest detail (full JSON), used by a future
        "preview manifest" UI. Looks up the same fallback chain
        as `GaptEnvironmentService`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gapt_server.agent.environment_service import SERVER_MANIFESTS_DIR
from gapt_server.container import get_db_session
from gapt_server.db import models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.routers.auth import get_current_user


router = APIRouter(prefix="/_gapt/api/manifests", tags=["manifests"])


class ManifestSummary(BaseModel):
    """Row in the picker dropdown. `id` is what gets sent back as
    `CreateSessionRequest.env_id`; everything else is descriptive."""

    id: str
    display_name: str
    description: str | None = None
    provider: str | None = None
    model: str | None = None
    source: str  # "bundled" | "workspace"
    tags: list[str] = []


class ManifestListResponse(BaseModel):
    manifests: list[ManifestSummary]
    default_manifest_id: str


def _read_manifest_file(path: Path) -> dict[str, Any] | None:
    """Parse a single manifest JSON. Returns `None` when the file is
    unreadable / malformed — list endpoint then skips it without
    erroring out the whole response."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _summarise(manifest_id: str, raw: dict[str, Any], source: str) -> ManifestSummary:
    """Project the on-disk manifest into the dropdown row shape.

    Pulls description / model / provider out of `metadata` + the
    `api` stage so the picker can render a useful one-liner without
    the operator opening the JSON."""
    metadata = raw.get("metadata") or {}
    name = raw.get("name") or manifest_id
    description = metadata.get("description")
    tags = metadata.get("tags") or []
    provider: str | None = None
    model: str | None = None
    for stage in raw.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        if stage.get("name") == "api":
            config = stage.get("config") or {}
            provider = config.get("provider")
            model = config.get("model")
            break
    return ManifestSummary(
        id=manifest_id,
        display_name=str(name),
        description=description,
        provider=provider,
        model=model,
        source=source,
        tags=[str(t) for t in tags if isinstance(t, str)],
    )


def _scan_dir(directory: Path, source: str) -> list[ManifestSummary]:
    if not directory.is_dir():
        return []
    rows: list[ManifestSummary] = []
    for entry in sorted(directory.glob("*.json")):
        manifest_id = entry.stem
        raw = _read_manifest_file(entry)
        if raw is None:
            continue
        rows.append(_summarise(manifest_id, raw, source))
    return rows


async def _workspace_manifest_dir(
    db: AsyncSession, workspace_id: str
) -> Path | None:
    """Resolve the `.gapt/manifests/` directory inside the
    workspace's worktree. Returns `None` when the workspace doesn't
    exist or has no worktree_path set."""
    row = await db.get(models.Workspace, workspace_id)
    if row is None or not row.worktree_path:
        return None
    candidate = Path(row.worktree_path) / ".gapt" / "manifests"
    return candidate if candidate.is_dir() else None


@router.get("", response_model=ManifestListResponse)
async def list_manifests(
    workspace_id: str | None = Query(default=None, description="Include the workspace's local manifests too."),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ManifestListResponse:
    """Server-bundled manifests always returned. Workspace-local
    manifests are appended when `workspace_id` is supplied — these
    *override* a bundled manifest with the same id (workspace wins;
    matches `GaptEnvironmentService`'s fallback chain).
    """
    from gapt_server.settings import get_settings  # noqa: PLC0415

    bundled = _scan_dir(SERVER_MANIFESTS_DIR, "bundled")
    by_id: dict[str, ManifestSummary] = {m.id: m for m in bundled}

    if workspace_id:
        ws_dir = await _workspace_manifest_dir(db, workspace_id)
        if ws_dir is not None:
            for m in _scan_dir(ws_dir, "workspace"):
                by_id[m.id] = m  # workspace overrides bundled

    settings = get_settings()
    return ManifestListResponse(
        manifests=sorted(by_id.values(), key=lambda m: m.id),
        default_manifest_id=settings.default_manifest_id,
    )


@router.get("/{manifest_id}")
async def get_manifest_detail(
    manifest_id: str,
    workspace_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Full manifest JSON. Same fallback chain as
    `GaptEnvironmentService`: workspace-local wins over bundled.
    Used by a future "preview manifest" / "edit manifest" UI; the
    picker uses the summary endpoint above."""
    if workspace_id:
        ws_dir = await _workspace_manifest_dir(db, workspace_id)
        if ws_dir is not None:
            candidate = ws_dir / f"{manifest_id}.json"
            if candidate.is_file():
                payload = _read_manifest_file(candidate)
                if payload is not None:
                    return {"source": "workspace", "manifest": payload}

    bundled_path = SERVER_MANIFESTS_DIR / f"{manifest_id}.json"
    if bundled_path.is_file():
        payload = _read_manifest_file(bundled_path)
        if payload is not None:
            return {"source": "bundled", "manifest": payload}

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "manifest.not_found",
            "reason": f"no manifest with id {manifest_id!r}",
        },
    )
