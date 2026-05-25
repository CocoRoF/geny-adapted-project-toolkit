"""Admin-global Agent / manifest preferences.

- `GET  /api/agent-prefs`  — current admin prefs (empty record if unset)
- `PUT  /api/agent-prefs`  — upsert; every field optional, null = clear

These overrides are read by `ProjectAwareSessionManager.create_session`
and `rehydrate_session` and patched into the loaded manifest via
`apply_overrides` before `Pipeline.from_manifest_async`. The on-disk
`gapt_default.json` is never modified — overrides are dynamic.

Scope: deliberately a single global row (single-admin model). The full
stage-graph editor lives in a future cycle.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic introspection at runtime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from gapt_server.agent.session_registry import SessionRegistry  # noqa: TC001
from gapt_server.container import get_db_session, get_session_registry
from gapt_server.db import models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# Singleton row id — every admin-scoped read/write targets this literal.
_ADMIN_PREFS_ID = "admin"


router = APIRouter(prefix="/api/agent-prefs", tags=["agent-prefs"])


# Lower bounds keep obvious abuse out (0 tokens = no response,
# negative budget = nonsense). The upper bounds line up with what
# `claude_code_cli` / `anthropic` will tolerate without 4xx-ing.
_PERMISSION_MODES = {"bypassPermissions", "acceptEdits", "default", "plan"}


class AgentPrefsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model: str | None = Field(default=None, max_length=80)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    max_iterations: int | None = Field(default=None, ge=1, le=100)
    cost_budget_usd: float | None = Field(default=None, ge=0.0, le=1_000.0)
    timeout_s: int | None = Field(default=None, ge=1, le=600)
    permission_mode: str | None = Field(default=None, max_length=40)

    def model_post_init(self, _: object) -> None:
        if self.permission_mode is not None and self.permission_mode not in _PERMISSION_MODES:
            raise ValueError(
                f"permission_mode must be one of {sorted(_PERMISSION_MODES)}; got {self.permission_mode!r}"
            )


class AgentPrefsResponse(AgentPrefsPayload):
    id: str | None = None
    updated_at: datetime | None = None


def _row_to_response(row: models.AdminAgentPrefs | None) -> AgentPrefsResponse:
    if row is None:
        return AgentPrefsResponse()
    return AgentPrefsResponse(
        id=row.id,
        model=row.model,
        max_tokens=row.max_tokens,
        max_iterations=row.max_iterations,
        cost_budget_usd=float(row.cost_budget_usd) if row.cost_budget_usd is not None else None,
        timeout_s=row.timeout_s,
        permission_mode=row.permission_mode,
        updated_at=row.updated_at,
    )


async def _fetch(db: AsyncSession) -> models.AdminAgentPrefs | None:
    return (
        await db.execute(
            select(models.AdminAgentPrefs).where(models.AdminAgentPrefs.id == _ADMIN_PREFS_ID)
        )
    ).scalar_one_or_none()


@router.get("", response_model=AgentPrefsResponse)
async def get_prefs(
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> AgentPrefsResponse:
    _ = user
    row = await _fetch(db)
    return _row_to_response(row)


@router.put("", response_model=AgentPrefsResponse, status_code=status.HTTP_200_OK)
async def put_prefs(
    payload: AgentPrefsPayload,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    registry: SessionRegistry = Depends(get_session_registry),  # noqa: B008
) -> AgentPrefsResponse:
    """Upsert — every field is optional; `null` clears that override.

    Also evicts any cached session runtimes so the next invoke / stream
    forces `rehydrate_session` to rebuild the pipeline with the fresh
    prefs. Without this eviction an operator who changes
    model / permission_mode / etc. while a session is open would keep
    talking to the *old* pipeline until they explicitly archive +
    restart — confusing UX ("I picked Opus but it still uses Sonnet").
    """
    # Single-row upsert keyed on the literal id="admin". SELECT-then-
    # INSERT/UPDATE keeps us off `ON CONFLICT` so the same code path
    # works on SQLite (test/dev) and Postgres (prod).
    row = await _fetch(db)
    if row is None:
        row = models.AdminAgentPrefs(
            id=_ADMIN_PREFS_ID,
            model=payload.model,
            max_tokens=payload.max_tokens,
            max_iterations=payload.max_iterations,
            cost_budget_usd=payload.cost_budget_usd,
            timeout_s=payload.timeout_s,
            permission_mode=payload.permission_mode,
        )
        db.add(row)
    else:
        row.model = payload.model
        row.max_tokens = payload.max_tokens
        row.max_iterations = payload.max_iterations
        row.cost_budget_usd = payload.cost_budget_usd
        row.timeout_s = payload.timeout_s
        row.permission_mode = payload.permission_mode
    await db.commit()
    # Evict cached runtimes so the next invoke rehydrates with the
    # new prefs. Best-effort — if the registry is in a weird state
    # we still want the PUT to succeed and return the saved row.
    try:
        await registry.invalidate_user(user.id)
    except Exception:
        pass
    row = await _fetch(db)
    return _row_to_response(row)
