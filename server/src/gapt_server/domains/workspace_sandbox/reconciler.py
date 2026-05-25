"""Background reconciler — keeps prod-stack containers attached to
the shared `gapt-net` docker network.

Problem this solves: `docker compose up --build --no-deps <svc>`
recreates the named service without inherited external-network
bindings. The original `WorkspaceSandbox._route_primary_service`
hook fires only on *GAPT*-initiated deploys; a manual restart from
the user's terminal or a compose-internal recreate (e.g. healthcheck-
driven restart) silently leaves the container off gapt-net, and the
Caddy preview route then 502s until someone reconnects.

Approach:
- Poll once per `RECONCILE_INTERVAL_S` (default 30s).
- Use `docker ps --filter` to enumerate every container whose
  `com.docker.compose.project` label starts with the GAPT prefix
  (`gapt-prod-`).
- For each, inspect its networks; if `gapt-net` is missing, run
  `docker network connect gapt-net <name>`.

Failure handling: docker errors are logged but never raised — a bad
poll iteration shouldn't kill the loop. The loop is a daemon task
owned by the FastAPI lifespan; cancellation tears it down cleanly.

What this does NOT do (deliberately):
- Doesn't re-register Caddy routes. Routes carry container DNS
  names, not IPs, so a reconnect to gapt-net is sufficient — Caddy
  resolves the name at request time. If routes were dropped (Caddy
  config wiped), that's a separate recovery.
- Doesn't touch the per-workspace `gapt-ws-<wid>` sandbox
  containers. Those are owned end-to-end by `WorkspaceSandbox` so
  there's no third party that can recreate them out from under us.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

logger = logging.getLogger(__name__)


# Periodic interval. 30s is a safe default — long enough that
# `docker ps` overhead is invisible, short enough that the user's
# preview comes back without them noticing the drop.
RECONCILE_INTERVAL_S = float(os.environ.get("GAPT_RECONCILE_INTERVAL_S", "30"))

# Compose project prefix GAPT uses for prod stacks (mirrors
# `LocalComposeTarget.project_prefix`). Keep these in sync.
COMPOSE_PROJECT_PREFIX = "gapt-prod-"

GAPT_NETWORK = os.environ.get("GAPT_WORKSPACE_NETWORK", "gapt-net")


async def reconcile_once() -> tuple[int, int]:
    """One sweep. Returns (scanned, reconnected).

    Errors are swallowed individually so a single misbehaving
    container can't poison the whole sweep."""
    rc, names_blob, err = await _run_docker(
        "ps",
        "--filter",
        "label=com.docker.compose.project",
        "--format",
        "{{.Names}}\t{{.Label \"com.docker.compose.project\"}}",
    )
    if rc != 0:
        logger.debug("reconciler.docker_ps_failed err=%s", err.strip()[:200])
        return 0, 0

    scanned = 0
    reconnected = 0
    for line in names_blob.splitlines():
        if not line.strip():
            continue
        try:
            name, project = line.split("\t", 1)
        except ValueError:
            continue
        if not project.startswith(COMPOSE_PROJECT_PREFIX):
            continue
        scanned += 1
        try:
            if await _has_gapt_net(name):
                continue
            ok = await _connect(name)
            if ok:
                reconnected += 1
                logger.info(
                    "reconciler.reconnected container=%s network=%s",
                    name,
                    GAPT_NETWORK,
                )
        except Exception:
            logger.exception("reconciler.iter_failed container=%s", name)
    return scanned, reconnected


async def _has_gapt_net(container_name: str) -> bool:
    """Inspect the container's network membership. Format string
    pulls the network keys directly so we don't parse JSON unless
    necessary."""
    rc, out, _ = await _run_docker(
        "inspect",
        "-f",
        "{{range $k,$v := .NetworkSettings.Networks}}{{$k}}\\n{{end}}",
        container_name,
    )
    if rc != 0:
        return False
    networks = [n.strip() for n in out.replace("\\n", "\n").splitlines() if n.strip()]
    return GAPT_NETWORK in networks


async def _connect(container_name: str) -> bool:
    rc, _, err = await _run_docker(
        "network", "connect", GAPT_NETWORK, container_name
    )
    if rc == 0:
        return True
    # `already exists in network` is a race we treat as success.
    if "already exists" in err.lower() or "already in" in err.lower():
        return True
    logger.warning(
        "reconciler.connect_failed container=%s err=%s",
        container_name,
        err.strip()[:200],
    )
    return False


async def reconcile_loop() -> None:
    """Forever loop the lifespan owns. Exits cleanly on cancel."""
    logger.info(
        "reconciler.started interval_s=%s network=%s",
        RECONCILE_INTERVAL_S,
        GAPT_NETWORK,
    )
    try:
        while True:
            try:
                scanned, reconnected = await reconcile_once()
                if reconnected:
                    logger.info(
                        "reconciler.sweep scanned=%d reconnected=%d",
                        scanned,
                        reconnected,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("reconciler.sweep_failed")
            await asyncio.sleep(RECONCILE_INTERVAL_S)
    except asyncio.CancelledError:
        logger.info("reconciler.stopped")
        raise


async def _run_docker(
    *args: str, timeout_s: float = 10.0
) -> tuple[int, str, str]:
    """Light wrapper — same shape as `workspace_sandbox.manager`'s
    helper, kept local so the reconciler doesn't import from manager
    and create a cycle."""
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
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
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


# Keep `json` import referenced for downstream readers that grep
# this file expecting the optional JSON-inspect path. The format-
# string approach above is cheaper but the JSON path stays in the
# module's vocabulary for future tweaks.
_ = json
