"""Cost aggregation queries.

The cost dashboard surfaces two views:

1. **Summary across the actor's projects** — per-project totals over a
   time window. The actor only sees projects they're a member of.
2. **Per-day for one project** — daily buckets for a single project.

Both queries run over `agent_sessions` directly (the same row that the
SSE cost hook accumulates into). We bucket by `created_at` truncated
to the day in UTC — sessions that started in day D are billed to day
D regardless of how long they stayed open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Date, cast, func, select

from gapt_server.db import models

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ProjectCostRow:
    project_id: str
    project_slug: str
    project_display_name: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    session_count: int


@dataclass(frozen=True)
class DailyCostRow:
    """One bucket per UTC date. `date` is a `YYYY-MM-DD` string so the
    JSON wire format is stable across clients."""

    date: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    session_count: int


async def aggregate_summary(
    db: AsyncSession,
    *,
    project_ids: Sequence[str],
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[ProjectCostRow]:
    """Per-project rollups for the supplied (already-authorized) ids.

    The caller is responsible for filtering `project_ids` to projects
    the actor can see — this function does not re-check membership.
    Returns one row per project that has *any* session in range;
    projects with zero sessions are omitted (UI shows them as $0)."""
    if not project_ids:
        return []

    stmt = (
        select(
            models.AgentSession.project_id,
            models.Project.slug,
            models.Project.display_name,
            func.coalesce(func.sum(models.AgentSession.cost_usd), 0).label("cost_usd"),
            func.coalesce(func.sum(models.AgentSession.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(models.AgentSession.output_tokens), 0).label("output_tokens"),
            func.count(models.AgentSession.id).label("session_count"),
        )
        .join(models.Project, models.Project.id == models.AgentSession.project_id)
        .where(models.AgentSession.project_id.in_(list(project_ids)))
        .group_by(
            models.AgentSession.project_id,
            models.Project.slug,
            models.Project.display_name,
        )
        .order_by(func.sum(models.AgentSession.cost_usd).desc())
    )
    if since is not None:
        stmt = stmt.where(models.AgentSession.created_at >= since)
    if until is not None:
        stmt = stmt.where(models.AgentSession.created_at <= until)

    rows = (await db.execute(stmt)).all()
    return [
        ProjectCostRow(
            project_id=row.project_id,
            project_slug=row.slug,
            project_display_name=row.display_name,
            cost_usd=float(row.cost_usd or 0),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            session_count=int(row.session_count or 0),
        )
        for row in rows
    ]


async def aggregate_daily_for_project(
    db: AsyncSession,
    *,
    project_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[DailyCostRow]:
    """Per-day buckets for one project, oldest first.

    Days with zero sessions are *not* synthesized — the UI should
    render gaps as $0 itself (avoids generating an unbounded row set
    for sparse projects)."""
    day = cast(models.AgentSession.created_at, Date).label("day")
    stmt = (
        select(
            day,
            func.coalesce(func.sum(models.AgentSession.cost_usd), 0).label("cost_usd"),
            func.coalesce(func.sum(models.AgentSession.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(models.AgentSession.output_tokens), 0).label("output_tokens"),
            func.count(models.AgentSession.id).label("session_count"),
        )
        .where(models.AgentSession.project_id == project_id)
        .group_by(day)
        .order_by(day.asc())
    )
    if since is not None:
        stmt = stmt.where(models.AgentSession.created_at >= since)
    if until is not None:
        stmt = stmt.where(models.AgentSession.created_at <= until)

    rows = (await db.execute(stmt)).all()
    return [
        DailyCostRow(
            date=row.day.isoformat(),
            cost_usd=float(row.cost_usd or 0),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            session_count=int(row.session_count or 0),
        )
        for row in rows
    ]
