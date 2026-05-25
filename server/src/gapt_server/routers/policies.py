"""Policy inspection endpoint.

`GET /api/policies` — read-only snapshot of the *currently effective*
policy table (built-in defaults merged with the server-wide L2 YAML
override that the container loaded at startup). Each row carries the
action, the resolved decision, and the source layer.

PUT-based editing (per plan §4.5) requires DB-backed L3 / L4
overrides which depend on a migration; that lands in a follow-up
cycle. Today the operator edits the YAML and restarts the server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from gapt_server.container import get_policy_engine
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.policy.config_loader import INVARIANT_FLOORS
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from gapt_server.policy.engine import PolicyEngine


router = APIRouter(prefix="/api/policies", tags=["policies"])


class PolicyRow(BaseModel):
    action: str
    decision: str
    source: str
    reason: str = ""
    invariant_floor: str | None = None


class PolicyTableResponse(BaseModel):
    rows: list[PolicyRow]
    invariants: dict[str, str]


@router.get("", response_model=PolicyTableResponse)
async def get_effective_policy(
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    policy_engine: PolicyEngine = Depends(get_policy_engine),  # noqa: B008
) -> PolicyTableResponse:
    table = policy_engine.effective_table()
    invariants = {action: floor.value for action, floor in INVARIANT_FLOORS.items()}
    rows = [
        PolicyRow(
            action=row["action"],
            decision=row["decision"],
            source=row["source"],
            reason=row.get("reason", ""),
            invariant_floor=invariants.get(row["action"]),
        )
        for row in table
    ]
    # `user` is gated by Depends; we don't surface it in the body but
    # the dependency forces the request to be authenticated.
    _ = user
    return PolicyTableResponse(rows=rows, invariants=invariants)
