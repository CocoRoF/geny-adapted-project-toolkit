"""Boot-time Caddy ↔ DB resynchronisation.

Called from `app.lifespan` after the orphan-cleanup passes. Two
separate concerns, kept distinct so the simpler half stays useful
when the more expensive half fails:

1. **Stale-route cleanup** (`cleanup_stale_routes`): walk the
   `gapt-preview-*` routes Caddy has in memory and drop any whose
   slug doesn't appear in the current DB. This is what fixes the
   "preview-domain typo left a stuck wildcard" class of bug — the
   route family that no longer corresponds to any environment is
   removed deterministically. Also drops the zone-wide catch-all
   when the preview_domain has changed, so the next register
   re-emits it with the right wildcard.

2. **Active-env replay** (`replay_active_environments`): for each
   environment with a `last_run.status="success"` snapshot, locate
   the still-running primary container via `StackManager.status`
   and re-register its Caddy route. Mirrors the bulk of what
   `stack_reroute` does but never raises — failures are logged so
   one bad env doesn't block the others.

Both passes are best-effort: if a step blows up we record + move
on so the server still boots into a usable state. The user can
always hit the per-env "Reroute" button to retry interactively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from gapt_server.db import models
from gapt_server.domains.caddy.admin_api import (
    CaddyAdminClient,
    CaddyHttpTransport,
)
from gapt_server.domains.caddy.subdomain import (
    PreviewMode,
    SubdomainBinding,
    SubdomainManager,
    ZONE_CATCHALL_ROUTE_ID,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from gapt_server.domains.deploy.stack_manager import StackManager
    from gapt_server.settings import Settings


logger = structlog.get_logger(__name__)


# Mirror of `_resolve_preview_slug` from routers/deploy.py. Kept
# private + tiny to avoid an import cycle (deploy router pulls in
# the whole stack manager + Caddy module surface).
_SLUG_AUTO = "prod-{name}-{project_id}"


def _resolve_slug(env: models.Environment) -> str:
    cfg = env.deploy_target_config if isinstance(env.deploy_target_config, dict) else {}
    override = cfg.get("preview_slug")
    if isinstance(override, str) and override.strip():
        return override.strip().lower()
    return _SLUG_AUTO.format(name=env.name, project_id=env.project_id).lower()


@dataclass
class ResyncReport:
    """What changed during a resync pass. Logged at info level; not
    surfaced to the API today but useful when the user files a
    "GAPT didn't recover after reboot" ticket."""

    stale_deleted: list[str] = field(default_factory=list)
    catchall_reset: bool = False
    replayed: list[str] = field(default_factory=list)  # slugs
    replay_failures: list[tuple[str, str]] = field(
        default_factory=list
    )  # (env_id, reason)
    skipped_no_stack: list[str] = field(default_factory=list)


# ────────────────────────────────────────── stale cleanup ──


async def cleanup_stale_routes(
    *,
    session_factory: async_sessionmaker,
    settings: Settings,
    report: ResyncReport,
) -> None:
    """Drop the zone catch-all when its wildcard doesn't match the
    current `preview_domain`. The active-env replay step then
    re-creates it with the correct value on the next register.

    Scope decision (incident on first boot, 2026-05-28): we do NOT
    auto-delete individual `gapt-preview-<slug>` routes from a
    DB-slug allowlist. The naive heuristic was too aggressive:

      - Workspace preview slugs (e.g. `blog`) also live under the
        `gapt-preview-*` namespace and aren't visible from a pure
        `Environment` query.
      - `Environment.deploy_target_config.preview_slug` can drift
        from the live route id (legacy bindings / manual overrides
        / archived envs whose stack is still up).

    Net effect of the prior version: the boot pass deleted live,
    healthy routes the operator then had to re-register by hand.
    Catchall mismatch is the only condition we can detect
    deterministically — that's what B-Hardening's safety-net
    route was built for, and it's the only case where deleting
    is strictly better than leaving it alone.

    `session_factory` is unused today; kept on the signature so a
    future "delete routes whose upstream container no longer
    exists" pass can reach the workspaces / deploys tables without
    another router rewrite.
    """
    _ = session_factory  # noqa: F841 — reserved for future passes

    if not settings.caddy_admin_url:
        return

    preview_domain = settings.caddy_preview_domain
    if not preview_domain:
        return

    transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
    client = CaddyAdminClient(transport=transport)

    try:
        config = await client.get("/config/apps/http/servers/main/routes")
    except Exception as exc:  # noqa: BLE001
        logger.warning("caddy.boot_resync.list_routes_failed", error=str(exc))
        return

    expected_wildcard = f"*.{preview_domain.rstrip('.').lower()}"
    for route in config or []:
        if not isinstance(route, dict):
            continue
        if route.get("@id") != ZONE_CATCHALL_ROUTE_ID:
            continue
        match_list = route.get("match") or []
        first_match = match_list[0] if match_list else {}
        hosts = first_match.get("host") or []
        if expected_wildcard in hosts:
            break
        try:
            await client.delete(f"/id/{ZONE_CATCHALL_ROUTE_ID}")
            report.catchall_reset = True
            logger.info(
                "caddy.boot_resync.catchall_reset",
                had=hosts,
                expected=expected_wildcard,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "caddy.boot_resync.catchall_delete_failed",
                error=str(exc),
            )
        break


# ───────────────────────────────────────── active replay ──


async def replay_active_environments(
    *,
    session_factory: async_sessionmaker,
    settings: Settings,
    stack_manager: StackManager,
    report: ResyncReport,
) -> None:
    """Re-register Caddy routes for every environment whose last
    deploy was a success AND whose compose stack is still running.

    Doesn't run network-connect / Cloudflare wildcard work — those
    self-heal on the next user-initiated deploy or reroute. The
    point here is: a stack that's running but with no Caddy route
    is invisible to users; one register call brings it back.
    """
    if not settings.caddy_admin_url or not settings.caddy_preview_domain:
        return

    transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
    manager = SubdomainManager(
        client=CaddyAdminClient(transport=transport),
        preview_domain=settings.caddy_preview_domain,
        gapt_apex_host=settings.caddy_apex_host,
    )

    async with session_factory() as db:
        envs = (await db.execute(select(models.Environment))).scalars().all()

    for env in envs:
        last_run = env.last_run if isinstance(env.last_run, dict) else {}
        if last_run.get("status") != "success":
            continue
        try:
            new_host = await _replay_one(env, manager, stack_manager, report)
        except Exception as exc:  # noqa: BLE001
            # Catch-all so a single bad env can't poison the loop.
            report.replay_failures.append((env.id, str(exc)))
            logger.warning(
                "caddy.boot_resync.replay_failed",
                env_id=env.id,
                error=str(exc),
            )
            continue
        # Refresh `env.last_run.bound_url` when the URL Caddy is now
        # serving doesn't match what the DB has. Without this the IDE
        # keeps showing a stale link (typically from an old
        # caddy_preview_domain value) and the operator's click 302s
        # to GAPT instead of the deployed app. The reroute button used
        # to be the only thing that fixed this — now boot_resync +
        # the periodic reconciler heal it transparently.
        if new_host is not None:
            await _refresh_bound_url(
                session_factory=session_factory,
                env_id=env.id,
                new_host=new_host,
            )


async def _replay_one(
    env: models.Environment,
    manager: SubdomainManager,
    stack_manager: StackManager,
    report: ResyncReport,
) -> str | None:
    """Inner per-env step. Raises on any failure so the caller can
    record + move on.

    Returns the user-visible URL host the route resolved to (e.g.
    ``hr-test.gapt.hrletsgo.me`` for subdomain mode) on success, or
    ``None`` when there was nothing to register (skip cases). The
    caller uses this to refresh `env.last_run.bound_url` — pre-fix
    this was missing and a Caddy replay would silently leave the DB
    pointing at the OLD preview-domain URL, which is what the IDE's
    "라이브 / https://…" link shows. The result: stack works, route
    works, but the link the operator clicks goes nowhere because the
    UI string is stale until "즉시 라우트 갱신" is clicked manually.
    """
    cfg = env.deploy_target_config if isinstance(env.deploy_target_config, dict) else {}

    # Skip when no stack is actually running for this project. The
    # status endpoint would otherwise hand us a stale snapshot —
    # better to leave the Caddy route absent than to register one
    # pointing at a non-existent container.
    s = await stack_manager.status(env.project_id)
    if s.total_count == 0:
        report.skipped_no_stack.append(env.id)
        return

    # Pick the primary service the same way `stack_reroute` does —
    # explicit override → reverse-proxy heuristic → frontend-named
    # → first running. Without this the boot replay can't agree
    # with what a manual reroute click would do, and the operator
    # sees the route point at a different container after restart.
    primary_service = cfg.get("primary_service")
    primary_port = cfg.get("primary_port") or 3000

    reverse_proxy_names = {"nginx", "proxy", "gateway", "traefik", "caddy", "envoy"}
    chosen = (
        next(
            (svc for svc in s.services if primary_service and svc.service == primary_service),
            None,
        )
        or next(
            (svc for svc in s.services if svc.service in reverse_proxy_names), None
        )
        or next(
            (svc for svc in s.services if svc.service in {"frontend", "web", "app"}),
            None,
        )
        or next((svc for svc in s.services if svc.status == "running"), None)
    )
    if chosen is None:
        report.skipped_no_stack.append(env.id)
        return None
    if cfg.get("primary_port") is None and chosen.service in reverse_proxy_names:
        primary_port = 80

    mode_str = str(cfg.get("preview_mode") or "path").lower()
    mode = PreviewMode.SUBDOMAIN if mode_str == "subdomain" else PreviewMode.PATH
    strip_opt = cfg.get("strip_prefix")
    strip_prefix = (True if strip_opt is None else bool(strip_opt)) and mode == PreviewMode.PATH

    upstream_scheme = (cfg.get("upstream_scheme") or "http").lower()
    upstream_host_header = cfg.get("upstream_host_header") or None
    upstream_tls_insecure = bool(cfg.get("upstream_tls_insecure", False))

    slug = _resolve_slug(env)
    binding = SubdomainBinding(
        workspace_slug=slug,
        upstream_host=chosen.container_name,
        upstream_port=int(primary_port),
        mode=mode,
        strip_prefix=strip_prefix,
        upstream_scheme=upstream_scheme,
        upstream_host_header=upstream_host_header,
        upstream_tls_insecure=upstream_tls_insecure,
    )
    host = await manager.register(binding)
    report.replayed.append(slug)
    logger.info(
        "caddy.boot_resync.route_replayed",
        env_id=env.id,
        slug=slug,
        upstream=f"{upstream_scheme}://{chosen.container_name}:{primary_port}",
        mode=mode_str,
        bound_url=f"https://{host}",
    )
    return host


# ────────────────────────────────────────────── entry ──


async def run_boot_resync(
    *,
    session_factory: async_sessionmaker,
    settings: Settings,
    stack_manager: StackManager,
) -> ResyncReport:
    """Public entry — runs both passes back-to-back. Returns a
    report so the caller can log a summary line.

    Order matters: stale cleanup first so the active replay isn't
    confused by orphaned routes that share a slug with a now-
    different environment. Both passes catch their own errors;
    this function only raises if its own arguments are malformed."""
    report = ResyncReport()
    await cleanup_stale_routes(
        session_factory=session_factory,
        settings=settings,
        report=report,
    )
    await replay_active_environments(
        session_factory=session_factory,
        settings=settings,
        stack_manager=stack_manager,
        report=report,
    )
    return report


# ──────────────────────────────────────── single-env entry ──


async def replay_single_environment(
    env_id: str,
    *,
    session_factory: async_sessionmaker,
    settings: Settings,
    stack_manager: StackManager,
) -> ResyncReport:
    """Phase N.2.7 — re-register the Caddy route for ONE env, using
    the same per-env logic ``replay_active_environments`` loops over.

    Used by two new strong-robustness paths:
      * ``update_environment`` auto-resync: when the operator edits
        routing-relevant fields (preview_mode / preview_slug /
        primary_service / primary_port / strip_prefix / upstream_*),
        we replay this env's route in the background so the operator
        doesn't have to remember to click "즉시 라우트 갱신".
      * Periodic reconciler loop in ``app.lifespan`` — every few
        minutes it walks active envs and re-registers, so a Caddy
        restart / out-of-band stack restart self-heals without the
        operator having to notice + click.

    Never raises — returns a report. Skips when:
      * Caddy isn't configured.
      * Env row is missing.
      * Env's last_run isn't a successful deploy (no stack to route to).
      * No stack is currently running for the env's project.
    """
    report = ResyncReport()
    if not settings.caddy_admin_url or not settings.caddy_preview_domain:
        return report

    transport = CaddyHttpTransport(base_url=settings.caddy_admin_url)
    manager = SubdomainManager(
        client=CaddyAdminClient(transport=transport),
        preview_domain=settings.caddy_preview_domain,
        gapt_apex_host=settings.caddy_apex_host,
    )

    async with session_factory() as db:
        env = (
            await db.execute(
                select(models.Environment).where(models.Environment.id == env_id)
            )
        ).scalar_one_or_none()
    if env is None:
        return report

    last_run = env.last_run if isinstance(env.last_run, dict) else {}
    if last_run.get("status") != "success":
        return report

    try:
        new_host = await _replay_one(env, manager, stack_manager, report)
    except Exception as exc:  # noqa: BLE001
        report.replay_failures.append((env.id, str(exc)))
        logger.warning(
            "caddy.replay_single.failed",
            env_id=env.id,
            error=str(exc),
        )
        return report
    if new_host is not None:
        await _refresh_bound_url(
            session_factory=session_factory,
            env_id=env.id,
            new_host=new_host,
        )
    return report


async def _refresh_bound_url(
    *,
    session_factory: async_sessionmaker,
    env_id: str,
    new_host: str,
) -> None:
    """Update ``env.last_run.bound_url`` (and the matching DeployRun
    row) when the URL Caddy is now serving doesn't match what the DB
    has. Idempotent — same input twice is a no-op.

    Why this exists: the IDE's "라이브 / https://…" link reads from
    DB, not Caddy. When `caddy_preview_domain` changes (typo fix,
    domain migration) or the slug rule changes between releases, the
    Caddy route gets re-registered with the new host but the DB blob
    keeps the old URL forever. The operator clicks the stale link,
    gets routed to GAPT (no matching Caddy route), and has to mash
    "즉시 라우트 갱신" to refresh the DB.

    Best-effort — failures are logged + swallowed so a flaky DB write
    doesn't kill the boot resync or the periodic reconciler.
    """
    new_url = f"https://{new_host}"
    try:
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(models.Environment).where(
                        models.Environment.id == env_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            last_run = dict(row.last_run or {})
            if last_run.get("bound_url") == new_url:
                return  # no drift
            last_run["bound_url"] = new_url
            row.last_run = last_run

            # Also bump the matching DeployRun row so the sidebar /
            # deploy detail panel show the same fresh URL. Best-effort
            # — `run_id` may be missing on very old envs that pre-date
            # the cached blob.
            run_id = last_run.get("run_id")
            if isinstance(run_id, str) and run_id:
                deploy_row = (
                    await db.execute(
                        select(models.DeployRun).where(
                            models.DeployRun.id == run_id
                        )
                    )
                ).scalar_one_or_none()
                if deploy_row is not None and deploy_row.bound_url != new_url:
                    deploy_row.bound_url = new_url
            await db.commit()
            logger.info(
                "caddy.bound_url.refreshed",
                env_id=env_id,
                new_url=new_url,
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "caddy.bound_url.refresh_failed", env_id=env_id, new_url=new_url
        )
