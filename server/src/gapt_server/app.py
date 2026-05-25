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
    metrics,
    notifications,
    oneshot,
    policies,
    preview,
    projects,
    secrets,
    services,
    sessions,
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
    # gapt-net reconciler — periodic sweep that keeps prod compose
    # stacks attached to the shared network. Compose `--no-deps`
    # restarts strip external nets; without this loop the user has
    # to `docker network connect` by hand. Daemon task; cancelled on
    # shutdown.
    import asyncio as _asyncio  # noqa: PLC0415

    from gapt_server.domains.workspace_sandbox.reconciler import (  # noqa: PLC0415
        reconcile_loop,
    )

    reconciler_task = _asyncio.create_task(
        reconcile_loop(), name="gapt-net-reconciler"
    )

    # ServiceRegistry recovery — scan running workspaces for live
    # `GAPT_SVC=` markers and rebuild the in-memory table. Without
    # this every server restart wipes the Services panel even
    # though `npm run dev` is still happily running inside each
    # container.
    if container.session_factory is not None:
        from sqlalchemy import select as _select  # noqa: PLC0415

        async with container.session_factory() as bg_db:
            rows = (
                await bg_db.execute(
                    _select(
                        models.Workspace.id, models.Workspace.worktree_path
                    ).where(
                        models.Workspace.status == enums.WorkspaceStatus.RUNNING
                    )
                )
            ).all()
            workspaces = [(r[0], r[1]) for r in rows]
        try:
            restored = await container.services.recover_from_containers(workspaces)
            if restored:
                logger.info("services.recovered", count=restored)
        except Exception:  # noqa: BLE001
            logger.exception("services.recover_failed")
    try:
        yield
    finally:
        reconciler_task.cancel()
        try:
            await reconciler_task
        except (BaseException,):  # noqa: BLE001 — cancellation is expected
            pass
        await container.aclose()
        logger.info("gapt.server.shutdown")


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
    app.include_router(auth.router)
    app.include_router(secrets.router)
    app.include_router(projects.router)
    app.include_router(workspaces.by_project)
    app.include_router(workspaces.by_id)
    app.include_router(sessions.by_project)
    app.include_router(sessions.by_id)
    app.include_router(audit.router)
    app.include_router(deploy.router)
    app.include_router(ci.router)
    app.include_router(preview.router)
    app.include_router(preview.ask_router)
    app.include_router(policies.router)
    app.include_router(cost.router)
    app.include_router(metrics.router)
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
    app.include_router(git.router)
    return app


app = create_app()
