"""Phase N — scaffold preset listing + project-from-scaffold endpoint.

Two surfaces:

  * ``GET  /_gapt/api/scaffolds``  — wizard step 2 preset cards
  * ``POST /_gapt/api/projects/scaffold`` — wizard step 4 commit
                                            (lands in N.2.5)

This module holds the listing route now. The create route is added
in N.2.5 when the pusher (N.2.4) + token resolver wiring is ready.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends

from gapt_server.domains.auth import AdminPrincipal  # noqa: TC001 — Depends runtime introspection
from gapt_server.domains.scaffolds.registry import all_presets
from gapt_server.routers.auth import get_current_user

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/_gapt/api/scaffolds", tags=["scaffolds"])


@router.get("", response_model=dict[str, Any])
async def list_scaffolds(
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Return every registered preset's summary shape.

    Auth-gated — the listing reveals which stacks GAPT will create on
    the operator's behalf, plus the per-preset option schemas the
    wizard uses to render its Step 3 form. The response is deterministic
    (same Python process → same order) so the front-end can cache."""
    del user  # only here to gate auth
    return {"presets": [p.to_summary_dict() for p in all_presets()]}
