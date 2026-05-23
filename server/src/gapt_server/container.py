"""Application-scope dependency container.

A single source for things that live as long as the FastAPI process:
the SQLAlchemy engine and its `async_sessionmaker`. Per-request
adapters (audit sink, secret vault, etc.) hang off this container in
later cycles.

Why a container instead of module-level globals: the engine needs
explicit `.dispose()` on shutdown, and tests want to swap in an
overridden DSN without monkey-patching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003  — Depends inspects at runtime
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request
from sqlalchemy.ext.asyncio import (  # noqa: TC002  — dataclass + Depends inspect at runtime
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from gapt_server.agent.environment_service import GaptEnvironmentService
from gapt_server.agent.session_manager import ProjectAwareSessionManager
from gapt_server.agent.session_registry import SessionRegistry
from gapt_server.db import create_engine, create_session_factory
from gapt_server.domains.audit.sink import (
    AuditSink,
    NullAuditSink,
    PostgresAuditSink,
)
from gapt_server.domains.notifications import (
    DiscordWebhookChannel,
    InMemoryChannel,
    NotificationService,
    SlackWebhookChannel,
)
from gapt_server.domains.sandbox import (
    MockSandboxBackend,
    SandboxBackend,
    SysboxBackend,
    make_default_client,
)
from gapt_server.observability.metrics import MetricsRegistry
from gapt_server.policy.config_loader import PolicyConfigError, load_yaml
from gapt_server.policy.engine import PolicyEngine
from gapt_server.settings import Settings, get_settings


def _engine_from_settings(settings: Settings, audit_sink: AuditSink) -> PolicyEngine:
    """Load the L2 YAML override (if configured) and build the engine."""
    from gapt_server.policy.engine import PolicyDecision  # noqa: PLC0415

    overrides: dict[str, PolicyDecision] = {}
    reasons: dict[str, str] = {}
    if settings.policy_config_path:
        try:
            override_set = load_yaml(settings.policy_config_path, source_layer="server")
        except PolicyConfigError:
            # The loader's stable code is preserved; we re-raise so a
            # bad config makes startup loud rather than silently
            # ignored. Operators want to know.
            raise
        for action, override in override_set.by_action.items():
            overrides[action] = override.decision
            if override.reason:
                reasons[action] = override.reason
    return PolicyEngine(audit_sink=audit_sink, overrides=overrides, override_reasons=reasons)


@dataclass
class AppContainer:
    settings: Settings
    engine: AsyncEngine | None
    session_factory: async_sessionmaker[AsyncSession] | None
    audit_sink: AuditSink
    sandbox_backend: SandboxBackend
    policy_engine: PolicyEngine
    env_service: GaptEnvironmentService
    session_manager: ProjectAwareSessionManager
    session_registry: SessionRegistry
    registry: MetricsRegistry
    notifications: NotificationService

    async def aclose(self) -> None:
        await self.session_registry.aclose()
        await self.audit_sink.aclose()
        if self.engine is not None:
            await self.engine.dispose()


def build_container(
    settings: Settings,
    *,
    audit_sink: AuditSink | None = None,
    sandbox_backend: SandboxBackend | None = None,
) -> AppContainer:
    """Construct the container without performing any I/O.

    If `settings.postgres_dsn` is unset, the container is still usable
    for unit tests that don't touch the DB (e.g. /health). DB-dependent
    code paths will raise a clear error when they ask for a session.

    Pass `audit_sink` to inject a custom sink (tests use
    `InMemoryAuditSink`). When unset, a `PostgresAuditSink` is built
    against the same engine (or `NullAuditSink` when no DSN).

    Pass `sandbox_backend` to inject a custom backend (tests use
    `MockSandboxBackend`). Otherwise the backend is picked based on
    `settings.sandbox_use_real_docker`.
    """
    if sandbox_backend is None:
        sandbox_backend = _build_default_sandbox(settings)

    env_service = GaptEnvironmentService()
    notifications = _build_notifications(settings)

    if settings.postgres_dsn is None:
        sink_noop = audit_sink or NullAuditSink()
        policy_noop = _engine_from_settings(settings, sink_noop)
        return AppContainer(
            settings=settings,
            engine=None,
            session_factory=None,
            audit_sink=sink_noop,
            sandbox_backend=sandbox_backend,
            policy_engine=policy_noop,
            env_service=env_service,
            session_manager=ProjectAwareSessionManager(
                env_service=env_service, audit_sink=sink_noop
            ),
            session_registry=SessionRegistry(),
            registry=MetricsRegistry(),
            notifications=notifications,
        )

    engine = create_engine(_coerce_async_dsn(str(settings.postgres_dsn)))
    factory = create_session_factory(engine)
    sink: AuditSink
    if audit_sink is not None:
        sink = audit_sink
    else:
        pg_sink = PostgresAuditSink(
            engine=engine,
            flush_interval_s=settings.audit_flush_interval_s,
            max_batch_size=settings.audit_max_batch_size,
        )
        sink = pg_sink
    return AppContainer(
        settings=settings,
        engine=engine,
        session_factory=factory,
        audit_sink=sink,
        sandbox_backend=sandbox_backend,
        policy_engine=_engine_from_settings(settings, sink),
        env_service=env_service,
        session_manager=ProjectAwareSessionManager(env_service=env_service, audit_sink=sink),
        session_registry=SessionRegistry(),
        registry=MetricsRegistry(),
        notifications=notifications,
    )


def _build_notifications(settings: Settings) -> NotificationService:
    """Assemble channels from the operator-set env. M1 is always
    operator-set; per-user URLs land with a settings UI later."""
    channels: list[InMemoryChannel | SlackWebhookChannel | DiscordWebhookChannel] = [
        InMemoryChannel()
    ]
    if settings.slack_webhook_url:
        channels.append(SlackWebhookChannel(settings.slack_webhook_url))
    if settings.discord_webhook_url:
        channels.append(DiscordWebhookChannel(settings.discord_webhook_url))
    return NotificationService(channels=channels)


def _build_default_sandbox(settings: Settings) -> SandboxBackend:
    if settings.sandbox_use_real_docker:
        return SysboxBackend(client=make_default_client())
    return MockSandboxBackend()


def _coerce_async_dsn(dsn: str) -> str:
    """Map driverless / sync DSN tokens to `postgresql+psycopg` so the
    SQLAlchemy async engine actually drives via psycopg's async path."""
    if dsn.startswith("postgresql+psycopg://"):
        return dsn
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


# ─────────────────────────────────────────────────────────── FastAPI Depends ─


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, AppContainer):
        # Fallback for tests that bypass create_app; provide a fresh
        # container based on env settings.
        container = build_container(get_settings())
        request.app.state.container = container
    return container


def get_app_settings(container: AppContainer = Depends(get_container)) -> Settings:  # noqa: B008
    return container.settings


def get_audit_sink(container: AppContainer = Depends(get_container)) -> AuditSink:  # noqa: B008
    return container.audit_sink


def get_sandbox_backend(
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> SandboxBackend:
    return container.sandbox_backend


async def get_db_session(
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> AsyncIterator[AsyncSession]:
    if container.session_factory is None:
        raise RuntimeError(
            "Database is not configured (GAPT_POSTGRES_DSN unset). "
            "Set the DSN before using DB-backed endpoints."
        )
    async with container.session_factory() as session:
        yield session


def get_policy_engine(
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> PolicyEngine:
    return container.policy_engine


def get_session_manager(
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> ProjectAwareSessionManager:
    return container.session_manager


def get_session_registry(
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> SessionRegistry:
    return container.session_registry


def get_notifications(
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> NotificationService:
    return container.notifications


def attach_container(app: FastAPI, container: AppContainer) -> None:
    """Attach the container to app.state. The caller is responsible for
    calling `await container.aclose()` on shutdown (done in lifespan)."""
    app.state.container = container
