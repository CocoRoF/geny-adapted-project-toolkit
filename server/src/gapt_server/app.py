from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gapt_server import __version__
from gapt_server.container import AppContainer, attach_container, build_container
from gapt_server.db import enums, models
from gapt_server.domains.audit.sink import PostgresAuditSink
from gapt_server.logging import configure_logging
from gapt_server.middleware.trace_id import TraceIdMiddleware
from gapt_server.observability.instruments import register_default_metrics
from gapt_server.routers import (
    agent_prefs,
    audit,
    auth,
    ci,
    cost,
    deploy,
    environments,
    git,
    health,
    introspect,
    llm_backends,
    manifests as manifests_router,
    notifications,
    oneshot,
    performance,
    policies,
    preview,
    projects,
    providers,
    scaffolds,
    secrets,
    services,
    sessions,
    system,
    terminal,
    tests,
    webhooks,
    workspaces,
)
from gapt_server.settings import Settings, get_settings

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    container: AppContainer = app.state.container
    logger.info(
        "gapt.server.startup",
        version=__version__,
        env=settings.env,
        port=settings.port,
        db_configured=container.engine is not None,
    )
    # PostgresAuditSink needs its flush task started before any handler
    # runs (otherwise queued events sit until shutdown).
    if isinstance(container.audit_sink, PostgresAuditSink):
        container.audit_sink.start()
    # Phase M.1 — start the SessionRegistry idle-sweep loop. Must run
    # *inside* lifespan (we need a running event loop); the registry
    # was constructed via `_make_session_registry(settings)` which
    # already applied the LRU + idle caps from Settings.
    container.session_registry.start_sweep()
    # Workspaces stuck in `creating` from a previous server life are
    # orphans now — the background clone task that owned them died
    # with that process. Flip them to `failed` so the UI shows the
    # right state + the user can retry. Idempotent: no-op if there's
    # no DB.
    if container.session_factory is not None:
        from sqlalchemy import update  # noqa: PLC0415 — startup-only

        async with container.session_factory() as db:
            await db.execute(
                update(models.Workspace)
                .where(models.Workspace.status == enums.WorkspaceStatus.CREATING)
                .values(status=enums.WorkspaceStatus.FAILED)
            )
            await db.commit()
    # One-shot bare-repo migration. Moves any legacy
    # `<workspace_root>/<slug>/.bare/` to the new
    # `<workspace_bare_root>/<slug>/` layout and rewrites the
    # affected worktrees' `.git` files. Idempotent — projects already
    # on the new layout are skipped. Without this every legacy
    # workspace's container would either (a) fail every `git` command
    # (no bare mount) or (b) leak the bare's parent dir into the
    # worktree's git status (the bug this layout fixes). See
    # `domains/workspaces/bare_migration.py`.
    if container.session_factory is not None:
        from gapt_server.domains.workspaces.bare_migration import (  # noqa: PLC0415
            run_bare_migration,
        )

        try:
            report = await run_bare_migration(
                session_factory=container.session_factory,
                # Hardcoded to match service.py's worktree-path shape
                # `/workspace/<slug>/<wid>` — paired with the workspace
                # creation default. If we ever expose a settable
                # workspace_root, update both together.
                workspace_root="/workspace",
                workspace_bare_root=settings.workspace_bare_root,
            )
            if report.migrated or report.skipped_target_exists or report.failed:
                logger.info(
                    "workspace.bare_migration.done",
                    migrated=len(report.migrated),
                    skipped_no_legacy=len(report.skipped_no_legacy),
                    skipped_target_exists=len(report.skipped_target_exists),
                    failed=len(report.failed),
                )
        except Exception:
            logger.exception("workspace.bare_migration.crashed")

    # Deploy runs whose task died with the previous server life
    # are now orphans too — flip them to `aborted` so the UI doesn't
    # show a forever-spinning bar.
    if container.session_factory is not None:
        from gapt_server.domains.deploy.registry import (  # noqa: PLC0415
            mark_orphan_runs_aborted,
        )

        try:
            flipped = await mark_orphan_runs_aborted(container.session_factory)
            if flipped:
                logger.info("deploy.orphan_runs_marked_aborted", count=flipped)
        except Exception:
            logger.exception("deploy.orphan_runs_mark_failed")
    # gapt-net reconciler — periodic sweep that keeps prod compose
    # stacks attached to the shared network. Compose `--no-deps`
    # restarts strip external nets; without this loop the user has
    # to `docker network connect` by hand. Daemon task; cancelled on
    # shutdown.
    import asyncio as _asyncio  # noqa: PLC0415

    from gapt_server.domains.workspace_sandbox.reconciler import (  # noqa: PLC0415
        reconcile_loop,
    )

    reconciler_task = _asyncio.create_task(reconcile_loop(), name="gapt-net-reconciler")

    # Phase N.2.7 — Caddy route reconciler. Periodic safety-net loop
    # that re-runs `run_boot_resync` every few minutes so any drift
    # between Caddy's in-memory routes and the DB's expected set
    # self-heals without the operator having to notice + click
    # "즉시 라우트 갱신". Catches:
    #
    #   * Caddy container restart (routes live in memory only, lost
    #     on `docker compose restart caddy`).
    #   * Compose stack restart out-of-band (operator ran `docker
    #     compose restart` in the terminal — container names change
    #     across some operations, leaving Caddy's dial target stale).
    #   * GAPT server clock skew or transient Caddy admin-API blip
    #     during boot that left a route un-replayed.
    #
    # Interval picked at 5 minutes — short enough that drift is
    # caught before the operator notices, long enough that the load
    # on the Caddy admin API stays trivial. Failures are logged but
    # the loop never dies (a single bad iteration shouldn't kill the
    # safety net for everyone).
    caddy_reconciler_task: _asyncio.Task[None] | None = None
    if container.session_factory is not None and settings.caddy_admin_url:
        from gapt_server.domains.caddy.boot_resync import (  # noqa: PLC0415
            run_boot_resync as _run_caddy_resync,
        )
        from gapt_server.domains.deploy.stack_manager import (  # noqa: PLC0415
            StackManager as _StackManager,
        )
        from gapt_server.domains.sandbox import (  # noqa: PLC0415
            make_default_client as _make_docker_client,
        )

        async def _caddy_reconcile_loop() -> None:
            interval_s = 300.0  # 5 min
            while True:
                try:
                    await _asyncio.sleep(interval_s)
                except _asyncio.CancelledError:
                    return
                try:
                    sm = _StackManager(client=_make_docker_client())
                    rep = await _run_caddy_resync(
                        session_factory=container.session_factory,
                        settings=settings,
                        stack_manager=sm,
                    )
                    if rep.replayed or rep.replay_failures:
                        logger.info(
                            "caddy.reconciler.tick",
                            replayed=len(rep.replayed),
                            failures=len(rep.replay_failures),
                            skipped_no_stack=len(rep.skipped_no_stack),
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("caddy.reconciler.crashed")

        caddy_reconciler_task = _asyncio.create_task(
            _caddy_reconcile_loop(), name="caddy-route-reconciler"
        )

    # Caddy ↔ DB resync — stale-route cleanup + active-env replay.
    # Stale cleanup is what fixes the "preview-domain typo left a
    # stuck wildcard" class of bug deterministically on every boot.
    # Replay re-registers the route family of any environment whose
    # last deploy succeeded AND whose compose stack is still up,
    # so a Caddy container restart / host reboot lands the user in
    # a working state without them clicking Reroute. Best-effort —
    # individual failures are logged, the server still comes up.
    if container.session_factory is not None and settings.caddy_admin_url:
        try:
            from gapt_server.domains.caddy.boot_resync import (  # noqa: PLC0415
                run_boot_resync,
            )
            from gapt_server.domains.deploy.stack_manager import (  # noqa: PLC0415
                StackManager,
            )
            from gapt_server.domains.sandbox import (  # noqa: PLC0415
                make_default_client,
            )

            sm = StackManager(client=make_default_client())
            resync_report = await run_boot_resync(
                session_factory=container.session_factory,
                settings=settings,
                stack_manager=sm,
            )
            logger.info(
                "caddy.boot_resync.done",
                stale_deleted=len(resync_report.stale_deleted),
                catchall_reset=resync_report.catchall_reset,
                replayed=len(resync_report.replayed),
                replay_failures=len(resync_report.replay_failures),
                skipped_no_stack=len(resync_report.skipped_no_stack),
            )
        except Exception:
            logger.exception("caddy.boot_resync.crashed")

    # ServiceRegistry recovery — converge each running workspace's
    # container to its durable service manifests: adopt the still-live
    # ones with full metadata, restart the ones that died while desired
    # running (Turbopack orphans included), and surface deliberately
    # stopped ones. Without this a server restart wiped the Services
    # panel and lost the declared port / env / preview binding.
    if container.session_factory is not None:
        from sqlalchemy import select as _select  # noqa: PLC0415

        async with container.session_factory() as bg_db:
            rows = (
                await bg_db.execute(
                    _select(models.Workspace.id, models.Workspace.worktree_path).where(
                        models.Workspace.status == enums.WorkspaceStatus.RUNNING
                    )
                )
            ).all()
            workspaces = [(r[0], r[1]) for r in rows]
        try:
            recovered = await container.services.reconcile_workspaces(workspaces)
            await _apply_reconcile_report(container, settings, recovered)
        except Exception:
            logger.exception("services.reconcile_failed")

    await _log_capability_warnings(settings)

    # GAPT self-introspection MCP (agent debugging window). The
    # FastMCP streamable-HTTP transport needs its session manager
    # running; mounted Starlette sub-apps don't get their lifespan
    # invoked by FastAPI, so we drive it here.
    mcp_app = getattr(app.state, "introspect_mcp", None)
    if mcp_app is not None:
        from contextlib import AsyncExitStack  # noqa: PLC0415

        async with AsyncExitStack() as mcp_stack:
            await mcp_stack.enter_async_context(mcp_app.session_manager.run())
            try:
                yield
            finally:
                reconciler_task.cancel()
                try:
                    await reconciler_task
                except BaseException:
                    pass
                if caddy_reconciler_task is not None:
                    caddy_reconciler_task.cancel()
                    try:
                        await caddy_reconciler_task
                    except BaseException:
                        pass
                await container.aclose()
                logger.info("gapt.server.shutdown")
        return
    try:
        yield
    finally:
        reconciler_task.cancel()
        try:
            await reconciler_task
        except BaseException:
            pass
        if caddy_reconciler_task is not None:
            caddy_reconciler_task.cancel()
            try:
                await caddy_reconciler_task
            except BaseException:
                pass
        await container.aclose()
        logger.info("gapt.server.shutdown")


async def _log_capability_warnings(settings: Settings) -> None:
    """Probe the workspace-sandbox dependency chain at startup and log a
    warning per missing link (docker CLI / daemon / sysbox runtime /
    image), so a misprovisioned host is visible in the logs — not only
    when a workspace-create fails. The SPA surfaces the same report via
    GET /_gapt/api/system/capabilities."""
    try:
        from gapt_server.domains.sandbox.capabilities import (  # noqa: PLC0415
            probe_capabilities,
        )

        report = await probe_capabilities(
            runtime=settings.sandbox_runtime, image=settings.sandbox_image_tag
        )
        if report.workspaces_ready:
            logger.info("capabilities.ok", detail="workspace sandboxes ready")
        else:
            for cap in report.missing:
                logger.warning(
                    "capabilities.missing",
                    capability=cap.key,
                    state=cap.state,
                    detail=cap.detail,
                    remedy=cap.remedy,
                )
    except Exception:
        logger.exception("capabilities.probe_failed")


async def _apply_reconcile_report(
    container: AppContainer,
    settings: Settings,
    recovered: list,  # list[RecoveredService]
) -> None:
    """Log the boot-reconcile outcome and re-establish the Caddy preview
    binding for services that were RESTARTED and had been exposed.

    Adopted-alive services keep their live Caddy route + loopback
    forwarder, and `_register_adopted` already restored their bound
    state from the manifest — nothing to do. A restarted service is a
    fresh process: its forwarder is gone and the route may point at a
    not-yet-listening port, so we re-run the same wait-then-register
    the restart endpoint uses. Fire-and-forget per service."""
    if not recovered:
        return
    import asyncio  # noqa: PLC0415

    from gapt_server.routers.services import (  # noqa: PLC0415
        _WARM_TASKS,
        _reexpose_when_ready,
    )

    by_action: dict[str, int] = {}
    for rec in recovered:
        by_action[rec.action] = by_action.get(rec.action, 0) + 1
    logger.info("services.reconciled", total=len(recovered), **by_action)

    for rec in recovered:
        if rec.action != "restarted" or not rec.expose:
            continue
        try:
            svc = await container.services.get(rec.workspace_id, rec.label)
        except Exception:
            continue
        task = asyncio.create_task(
            _reexpose_when_ready(
                registry=container.services,
                settings=settings,
                workspace_id=rec.workspace_id,
                label=rec.label,
                worktree_path=svc.worktree_path,
            ),
            name=f"reexpose-boot-{rec.workspace_id}-{rec.label}",
        )
        _WARM_TASKS.add(task)
        task.add_done_callback(_WARM_TASKS.discard)


def create_app(
    settings: Settings | None = None, *, container: AppContainer | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    container = container or build_container(settings)

    app = FastAPI(
        title="GAPT control plane",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    attach_container(app, container)
    register_default_metrics(container)

    # Trace-id binding runs *first* so every other middleware + handler
    # logs against the same request id.
    app.add_middleware(TraceIdMiddleware, header_name=settings.request_id_header)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(secrets.router)
    app.include_router(scaffolds.router)
    app.include_router(scaffolds.projects_router)
    app.include_router(projects.router)
    app.include_router(workspaces.by_project)
    app.include_router(workspaces.by_id)
    app.include_router(sessions.by_project)
    app.include_router(sessions.by_id)
    app.include_router(audit.router)
    app.include_router(deploy.router)
    app.include_router(deploy.runs_router)
    app.include_router(ci.router)
    app.include_router(preview.router)
    app.include_router(preview.ask_router)
    app.include_router(policies.router)
    app.include_router(cost.router)
    app.include_router(performance.router)
    app.include_router(llm_backends.router)
    app.include_router(manifests_router.router)
    app.include_router(notifications.router)
    app.include_router(oneshot.router)
    app.include_router(agent_prefs.router)
    app.include_router(terminal.router)
    app.include_router(services.router)
    app.include_router(environments.by_project)
    app.include_router(environments.by_id)
    app.include_router(introspect.router)
    app.include_router(webhooks.router)
    app.include_router(tests.router)
    app.include_router(providers.router)
    app.include_router(git.router)

    # Agent self-introspection MCP — mounted under the /_gapt/api/*
    # namespace so it rides the existing Caddy route and is reachable
    # from workspace containers over gapt-net. Bearer-token gated;
    # tokens are minted per session (see routers/sessions).
    try:
        from gapt_server.agent.introspect_mcp import build_introspect_app  # noqa: PLC0415

        mcp_app = build_introspect_app(container)
        app.state.introspect_mcp = mcp_app
        app.mount("/_gapt/api/mcp", mcp_app)
    except Exception:
        # MCP SDK missing / transport init failure must never block
        # the control plane itself.
        logger.exception("introspect_mcp.mount_failed")
        app.state.introspect_mcp = None
    return app


app = create_app()
