from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gapt_server import __version__
from gapt_server.container import AppContainer, attach_container, build_container
from gapt_server.domains.audit.sink import PostgresAuditSink
from gapt_server.logging import configure_logging
from gapt_server.middleware.trace_id import TraceIdMiddleware
from gapt_server.observability.instruments import register_default_metrics
from gapt_server.routers import (
    audit,
    auth,
    ci,
    cost,
    deploy,
    health,
    metrics,
    notifications,
    policies,
    preview,
    projects,
    secrets,
    sessions,
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
    try:
        yield
    finally:
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
    app.include_router(policies.router)
    app.include_router(cost.router)
    app.include_router(metrics.router)
    app.include_router(notifications.router)
    return app


app = create_app()
