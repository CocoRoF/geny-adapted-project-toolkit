"""Audit log query endpoint.

`GET /api/projects/{pid}/audit` returns the project's audit rows
sorted by ts descending. Filters: `action` prefix, `outcome`,
`since` / `until` ISO-8601 timestamps. The membership check piggybacks
on `fetch_project_for` — same gate as workspaces and sessions.

Pagination is offset-based today; a `seq`-cursor pagination wraps in
M2 once we partition the table monthly.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime  # noqa: TC003 — pydantic introspection
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from gapt_server.container import get_db_session
from gapt_server.db import enums, models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


router = APIRouter(prefix="/api/projects", tags=["audit"])


class AuditEntry(BaseModel):
    id: str
    ts: datetime
    actor_type: enums.AuditActorType
    actor_id: str | None
    scope: dict[str, Any]
    action: str
    subject: dict[str, Any]
    outcome: enums.AuditOutcome
    duration_ms: int | None
    exec_code: str | None
    payload: dict[str, Any]


@router.get("/{project_id}/audit", response_model=list[AuditEntry])
async def list_project_audit(
    project_id: str,
    action_prefix: str | None = Query(default=None, max_length=120),
    outcome: enums.AuditOutcome | None = Query(default=None),  # noqa: B008
    since: datetime | None = Query(default=None),  # noqa: B008
    until: datetime | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> list[AuditEntry]:
    try:
        await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    # JSONB `scope->>'project_id'` filter — uses the composite
    # index `(ts, scope_jsonb->>'project_id')` (M1-E1 migration).
    stmt = select(models.AuditEvent).where(
        models.AuditEvent.scope["project_id"].astext == project_id
    )
    if action_prefix:
        stmt = stmt.where(models.AuditEvent.action.startswith(action_prefix))
    if outcome is not None:
        stmt = stmt.where(models.AuditEvent.outcome == outcome)
    if since is not None:
        stmt = stmt.where(models.AuditEvent.ts >= since)
    if until is not None:
        stmt = stmt.where(models.AuditEvent.ts <= until)
    stmt = stmt.order_by(models.AuditEvent.ts.desc()).limit(limit).offset(offset)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        AuditEntry(
            id=row.id,
            ts=row.ts,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            scope=row.scope,
            action=row.action,
            subject=row.subject,
            outcome=row.outcome,
            duration_ms=row.duration_ms,
            exec_code=row.exec_code,
            payload=row.payload,
        )
        for row in rows
    ]


_CSV_FIELDS = (
    "id",
    "ts",
    "actor_type",
    "actor_id",
    "action",
    "outcome",
    "duration_ms",
    "exec_code",
    "scope",
    "subject",
    "payload",
)

_EXPORT_MAX = 5000


@router.get("/{project_id}/audit/export")
async def export_project_audit(
    project_id: str,
    format: str = Query(default="csv", pattern="^(csv|jsonl)$"),
    action_prefix: str | None = Query(default=None, max_length=120),
    outcome: enums.AuditOutcome | None = Query(default=None),  # noqa: B008
    since: datetime | None = Query(default=None),  # noqa: B008
    until: datetime | None = Query(default=None),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """Stream the filtered audit rows as CSV or JSONL.

    Bounded to `_EXPORT_MAX` rows per request so a huge filter
    doesn't blow memory. Operators who need more should narrow the
    time range or page through `GET /audit` instead."""
    try:
        await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    stmt = select(models.AuditEvent).where(
        models.AuditEvent.scope["project_id"].astext == project_id
    )
    if action_prefix:
        stmt = stmt.where(models.AuditEvent.action.startswith(action_prefix))
    if outcome is not None:
        stmt = stmt.where(models.AuditEvent.outcome == outcome)
    if since is not None:
        stmt = stmt.where(models.AuditEvent.ts >= since)
    if until is not None:
        stmt = stmt.where(models.AuditEvent.ts <= until)
    stmt = stmt.order_by(models.AuditEvent.ts.desc()).limit(_EXPORT_MAX)

    rows = (await db.execute(stmt)).scalars().all()

    if format == "jsonl":
        media = "application/x-ndjson"
        body = _render_jsonl(rows)
        filename = f"audit-{project_id}.jsonl"
    elif format == "csv":
        media = "text/csv"
        body = _render_csv(rows)
        filename = f"audit-{project_id}.csv"
    else:
        # The Query regex already rules this out, but mypy keeps
        # asking — exhaustive guard.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "audit.export.bad_format", "reason": format},
        )

    return StreamingResponse(
        iter([body]),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render_csv(rows: Sequence[models.AuditEvent]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_CSV_FIELDS))
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "id": row.id,
                "ts": row.ts.isoformat(),
                "actor_type": row.actor_type.value,
                "actor_id": row.actor_id or "",
                "action": row.action,
                "outcome": row.outcome.value,
                "duration_ms": "" if row.duration_ms is None else row.duration_ms,
                "exec_code": row.exec_code or "",
                "scope": json.dumps(row.scope, sort_keys=True),
                "subject": json.dumps(row.subject, sort_keys=True),
                "payload": json.dumps(row.payload, sort_keys=True),
            }
        )
    return buf.getvalue().encode("utf-8")


def _render_jsonl(rows: Sequence[models.AuditEvent]) -> bytes:
    lines: list[str] = []
    for row in rows:
        lines.append(
            json.dumps(
                {
                    "id": row.id,
                    "ts": row.ts.isoformat(),
                    "actor_type": row.actor_type.value,
                    "actor_id": row.actor_id,
                    "action": row.action,
                    "outcome": row.outcome.value,
                    "duration_ms": row.duration_ms,
                    "exec_code": row.exec_code,
                    "scope": row.scope,
                    "subject": row.subject,
                    "payload": row.payload,
                },
                separators=(",", ":"),
            )
        )
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


# Status field surfaced as a no-op symbol so callers can do
# `status.HTTP_200_OK` without re-importing.
_ = status
