"""Background workspace idle reaper.

Stops workspaces that have been RUNNING with no activity for longer than
``settings.sandbox_idle_pause_s``. Without this loop, every ``gapt-ws-<wid>``
container an agent ever opened keeps running indefinitely — eating host RAM/CPU
and occupying the active-workspace cap (``max_active_sandboxes`` counts CREATING
+ RUNNING) until the operator stops them by hand. The reaper transitions idle
ones to STOPPED, which frees the cap slot + the container while keeping the
workspace row so the user can start it again on demand.

Owned by the FastAPI lifespan as a daemon task; cancelled cleanly on shutdown.
Mirrors the gapt-net reconciler's loop shape (swallow per-iteration errors so a
single bad sweep never kills the loop).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from gapt_server.domains.workspaces.service import WorkspaceService

if TYPE_CHECKING:
    from gapt_server.container import AppContainer
    from gapt_server.settings import Settings

logger = structlog.get_logger(__name__)

# Sweep cadence. Checking far more often than the idle window buys nothing, so
# cap the interval at 5 min; keep a 60s floor so a small idle window (tests /
# tuned installs) still gets timely sweeps.
_INTERVAL_FLOOR_S = 60
_INTERVAL_CEIL_S = 300


def _build_service(container: AppContainer, settings: Settings) -> WorkspaceService:
    """A WorkspaceService wired for the stop path only (no clone/credentials
    needed to reap). Mirrors ``routers.workspaces.get_workspace_service``."""
    return WorkspaceService(
        sandbox_backend=container.sandbox_backend,
        sandbox_image=settings.sandbox_image_tag,
        audit_sink=container.audit_sink,
        session_factory=container.session_factory,
        workspace_sandbox=container.workspace_sandbox,
        max_active_sandboxes=settings.max_active_sandboxes,
        workspace_bare_root=settings.workspace_bare_root,
    )


async def reaper_loop(container: AppContainer, settings: Settings) -> None:
    """Forever loop the lifespan owns. Exits cleanly on cancel."""
    idle_s = settings.sandbox_idle_pause_s
    if container.session_factory is None or idle_s <= 0:
        logger.info("workspace.reaper.disabled", idle_pause_s=idle_s)
        return
    interval = max(_INTERVAL_FLOOR_S, min(idle_s, _INTERVAL_CEIL_S))
    svc = _build_service(container, settings)
    logger.info(
        "workspace.reaper.started", idle_pause_s=idle_s, interval_s=interval
    )
    try:
        while True:
            try:
                await svc.reap_idle_workspaces(idle_seconds=idle_s)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("workspace.reaper.sweep_failed", exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("workspace.reaper.stopped")
        raise
