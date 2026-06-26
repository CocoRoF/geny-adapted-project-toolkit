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
import contextlib
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


_WS_PREFIX = "gapt-ws-"


async def _docker(*args: str, timeout_s: float = 15.0) -> tuple[int, str, str]:
    """Minimal local docker wrapper (kept here so the reaper doesn't import
    from the sandbox manager). Errors return a non-zero rc, never raise."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return (-1, "", "docker not on PATH")
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        return (-1, "", "timed out")
    return (
        proc.returncode if proc.returncode is not None else -1,
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
    )


async def _reconcile_orphan_containers(container: AppContainer) -> int:
    """Stop ``gapt-ws-<wid>`` containers that are UP while their workspace
    sits in a terminal (non-running) DB state — drift from a stop/archive
    whose container teardown didn't land (the bug that left days-old idle
    containers piling up). Additionally removes the container when the
    workspace is ARCHIVED (gone for good). Returns the number acted on.

    Conservative on purpose: only acts on workspaces explicitly in
    STOPPED/FAILED/ARCHIVED. Active/creating/paused — and containers with
    no matching row (possibly mid-creation) — are left untouched, so a
    just-booted container is never killed out from under a live session."""
    sf = container.session_factory
    if sf is None:
        return 0
    rc, out, _ = await _docker(
        "ps", "--filter", f"name={_WS_PREFIX}", "--format", "{{.Names}}"
    )
    if rc != 0:
        return 0
    names = [n.strip() for n in out.splitlines() if n.strip().startswith(_WS_PREFIX)]
    if not names:
        return 0

    from sqlalchemy import select  # noqa: PLC0415
    from gapt_server.db import enums, models  # noqa: PLC0415

    async with sf() as db:
        rows = (
            await db.execute(select(models.Workspace.id, models.Workspace.status))
        ).all()
    # DB stores the ULID upper-case; docker lower-cases names → match CI.
    status_ci = {str(r[0]).lower(): r[1] for r in rows}
    terminal = {
        enums.WorkspaceStatus.STOPPED,
        enums.WorkspaceStatus.FAILED,
        enums.WorkspaceStatus.ARCHIVED,
    }
    acted = 0
    for name in names:
        st = status_ci.get(name[len(_WS_PREFIX):].lower())
        if st not in terminal:
            continue
        await _docker("stop", name)
        if st == enums.WorkspaceStatus.ARCHIVED:
            await _docker("rm", "-f", name)
        acted += 1
        logger.info(
            "workspace.reaper.orphan_container", container=name, db_status=str(st)
        )
    if acted:
        logger.info("workspace.reaper.orphans_reconciled", count=acted)
    return acted


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
                await _reconcile_orphan_containers(container)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("workspace.reaper.sweep_failed", exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("workspace.reaper.stopped")
        raise
