"""System capability endpoint.

Surfaces the workspace-sandbox dependency probe (Docker CLI, Docker
daemon, the sysbox runtime, the workspace image) so the SPA can warn
the operator when workspace creation won't work — instead of the
create failing opaquely at click time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from gapt_server.container import get_app_settings

# AdminPrincipal stays a runtime import: FastAPI resolves the Depends
# annotation at request time, so it can't move into a TYPE_CHECKING block.
from gapt_server.domains.auth import AdminPrincipal  # noqa: TC001
from gapt_server.domains.sandbox.capabilities import probe_capabilities
from gapt_server.routers.auth import get_current_user

if TYPE_CHECKING:
    from gapt_server.settings import Settings

router = APIRouter(prefix="/_gapt/api/system", tags=["system"])


@router.get("/capabilities")
async def get_capabilities(
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> dict[str, object]:
    """Probe the workspace-sandbox dependency chain. Returns
    ``{workspaces_ready, capabilities:[{key,label,state,detail,remedy}]}``.
    Cheap (a `docker info` + image inspect) but does touch the daemon,
    so it's behind auth like the rest of the control plane."""
    _ = user
    report = await probe_capabilities(
        runtime=settings.sandbox_runtime,
        image=settings.sandbox_image_tag,
    )
    return report.to_json()
