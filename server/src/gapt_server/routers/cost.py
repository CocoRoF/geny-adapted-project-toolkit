"""Cost dashboard endpoints.

Two views:

- `GET /api/cost/summary?since&until` — actor's projects, totals over
  the window. Always filtered to projects the actor is a member of.
- `GET /api/projects/{pid}/cost/daily?since&until` — daily buckets
  for one project (membership gated via `fetch_project_for`).

The numbers come from `agent_sessions` directly — same source the SSE
cost hook writes to. No separate billing table in M1; if/when we add
fine-grained per-message metering, the aggregation moves to that table
without changing the wire shape.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic introspection
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from gapt_server.container import get_db_session
from gapt_server.db import models
from gapt_server.domains.cost.service import (
    aggregate_daily_for_project,
    aggregate_summary,
)
from gapt_server.domains.projects.service import ProjectError, fetch_project_for
from gapt_server.routers.auth import get_current_user
from gapt_server.routers.projects import http_from_project_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


router = APIRouter(prefix="/api", tags=["cost"])


class CostSummaryRow(BaseModel):
    project_id: str
    project_slug: str
    project_display_name: str
    org_id: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    session_count: int


class CostSummary(BaseModel):
    rows: list[CostSummaryRow]
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int


class DailyCostRow(BaseModel):
    date: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    session_count: int


@router.get("/cost/summary", response_model=CostSummary)
async def get_cost_summary(
    since: datetime | None = Query(default=None),  # noqa: B008
    until: datetime | None = Query(default=None),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> CostSummary:
    # Authorize: only projects the actor has a membership on.
    project_ids = (
        await db.execute(
            select(models.ProjectMembership.project_id).where(
                models.ProjectMembership.user_id == user.id
            )
        )
    ).scalars().all()

    rows = await aggregate_summary(db, project_ids=project_ids, since=since, until=until)
    out_rows = [
        CostSummaryRow(
            project_id=r.project_id,
            project_slug=r.project_slug,
            project_display_name=r.project_display_name,
            org_id=r.org_id,
            cost_usd=round(r.cost_usd, 6),
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            session_count=r.session_count,
        )
        for r in rows
    ]
    return CostSummary(
        rows=out_rows,
        total_cost_usd=round(sum(r.cost_usd for r in out_rows), 6),
        total_input_tokens=sum(r.input_tokens for r in out_rows),
        total_output_tokens=sum(r.output_tokens for r in out_rows),
    )


@router.get("/projects/{project_id}/cost/daily", response_model=list[DailyCostRow])
async def get_project_cost_daily(
    project_id: str,
    since: datetime | None = Query(default=None),  # noqa: B008
    until: datetime | None = Query(default=None),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> list[DailyCostRow]:
    try:
        await fetch_project_for(db, actor=user, project_id=project_id)
    except ProjectError as exc:
        raise http_from_project_error(exc) from exc

    rows = await aggregate_daily_for_project(
        db, project_id=project_id, since=since, until=until
    )
    return [
        DailyCostRow(
            date=r.date,
            cost_usd=round(r.cost_usd, 6),
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            session_count=r.session_count,
        )
        for r in rows
    ]
