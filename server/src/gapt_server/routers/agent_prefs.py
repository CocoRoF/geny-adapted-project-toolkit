"""User-global Agent / manifest preferences.

- `GET  /api/agent-prefs`  — current user's prefs (empty record if unset)
- `PUT  /api/agent-prefs`  — upsert; every field optional, null = clear

These overrides are read by `ProjectAwareSessionManager.create_session`
and `rehydrate_session` and patched into the loaded manifest via
`apply_overrides` before `Pipeline.from_manifest_async`. The on-disk
`gapt_default.json` is never modified — overrides are dynamic.

Scope: deliberately a single row per user, not per project. Per-
project overrides (and the full stage-graph editor) would land later
when the surface area justifies the UX cost.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic introspection at runtime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gapt_server.container import get_db_session
from gapt_server.db import models  # noqa: TC001 — runtime introspection
from gapt_server.db.ulid import new_ulid
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


router = APIRouter(prefix="/api/agent-prefs", tags=["agent-prefs"])


# Lower bounds keep obvious abuse out (0 tokens = no response,
# negative budget = nonsense). The upper bounds line up with what
# `claude_code_cli` / `anthropic` will tolerate without 4xx-ing.
class AgentPrefsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model: str | None = Field(default=None, max_length=80)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    max_iterations: int | None = Field(default=None, ge=1, le=100)
    cost_budget_usd: float | None = Field(default=None, ge=0.0, le=1_000.0)
    timeout_s: int | None = Field(default=None, ge=1, le=600)


class AgentPrefsResponse(AgentPrefsPayload):
    id: str | None = None
    updated_at: datetime | None = None


def _row_to_response(row: models.UserAgentPrefs | None) -> AgentPrefsResponse:
    if row is None:
        return AgentPrefsResponse()
    return AgentPrefsResponse(
        id=row.id,
        model=row.model,
        max_tokens=row.max_tokens,
        max_iterations=row.max_iterations,
        cost_budget_usd=float(row.cost_budget_usd) if row.cost_budget_usd is not None else None,
        timeout_s=row.timeout_s,
        updated_at=row.updated_at,
    )


async def _fetch(db: AsyncSession, user_id: str) -> models.UserAgentPrefs | None:
    return (
        await db.execute(
            select(models.UserAgentPrefs).where(models.UserAgentPrefs.user_id == user_id)
        )
    ).scalar_one_or_none()


@router.get("", response_model=AgentPrefsResponse)
async def get_prefs(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> AgentPrefsResponse:
    row = await _fetch(db, user.id)
    return _row_to_response(row)


@router.put("", response_model=AgentPrefsResponse, status_code=status.HTTP_200_OK)
async def put_prefs(
    payload: AgentPrefsPayload,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: models.User = Depends(get_current_user),  # noqa: B008
) -> AgentPrefsResponse:
    """Upsert — every field is optional; `null` clears that override."""
    values = {
        "id": new_ulid(),
        "user_id": user.id,
        "model": payload.model,
        "max_tokens": payload.max_tokens,
        "max_iterations": payload.max_iterations,
        "cost_budget_usd": payload.cost_budget_usd,
        "timeout_s": payload.timeout_s,
    }
    stmt = pg_insert(models.UserAgentPrefs).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[models.UserAgentPrefs.user_id],
        set_={
            "model": payload.model,
            "max_tokens": payload.max_tokens,
            "max_iterations": payload.max_iterations,
            "cost_budget_usd": payload.cost_budget_usd,
            "timeout_s": payload.timeout_s,
        },
    )
    await db.execute(stmt)
    await db.commit()
    row = await _fetch(db, user.id)
    return _row_to_response(row)
