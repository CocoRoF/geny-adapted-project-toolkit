"""GAPT-specific metric definitions + live collectors.

`register_default_metrics(container)` is called from app startup so
that /metrics has something to render even before traffic. The live
gauges (`gapt_sessions_active`, `gapt_sandbox_count`) install async
collectors that query the DB on each scrape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from gapt_server.db import enums, models

if TYPE_CHECKING:
    from collections.abc import Mapping

    from gapt_server.container import AppContainer
    from gapt_server.observability.metrics import Counter, Gauge, MetricsRegistry


def cost_counter(reg: MetricsRegistry) -> Counter:
    return reg.counter(
        "gapt_agent_cost_usd_total",
        "Cumulative USD billed across agent sessions (labels: project_id).",
        ("project_id",),
    )


def input_tokens_counter(reg: MetricsRegistry) -> Counter:
    return reg.counter(
        "gapt_agent_input_tokens_total",
        "Cumulative agent prompt tokens (labels: project_id).",
        ("project_id",),
    )


def output_tokens_counter(reg: MetricsRegistry) -> Counter:
    return reg.counter(
        "gapt_agent_output_tokens_total",
        "Cumulative agent completion tokens (labels: project_id).",
        ("project_id",),
    )


def sessions_active_gauge(reg: MetricsRegistry) -> Gauge:
    return reg.gauge(
        "gapt_sessions_active",
        "Number of agent sessions in status=ACTIVE.",
    )


def sandbox_count_gauge(reg: MetricsRegistry) -> Gauge:
    return reg.gauge(
        "gapt_sandbox_count",
        "Number of sandbox containers per state (labels: state).",
        ("state",),
    )


def register_default_metrics(container: AppContainer) -> MetricsRegistry:
    """Register every gauge/counter we'll ever emit + wire DB-backed
    collectors against `container.session_factory`. Idempotent — the
    underlying registry dedupes by name."""
    reg = container.registry
    cost_counter(reg)
    input_tokens_counter(reg)
    output_tokens_counter(reg)

    sessions = sessions_active_gauge(reg)
    sandboxes = sandbox_count_gauge(reg)

    if container.session_factory is None:
        return reg

    factory = container.session_factory

    async def _collect_active() -> Mapping[tuple[str, ...], float]:
        async with factory() as db:
            total = (
                await db.execute(
                    select(func.count(models.AgentSession.id)).where(
                        models.AgentSession.status == enums.AgentSessionStatus.ACTIVE
                    )
                )
            ).scalar_one()
        return {(): float(total or 0)}

    async def _collect_sandboxes() -> Mapping[tuple[str, ...], float]:
        async with factory() as db:
            rows = (
                await db.execute(
                    select(
                        models.Sandbox.status,
                        func.count(models.Sandbox.id),
                    ).group_by(models.Sandbox.status)
                )
            ).all()
        return {(row[0].value,): float(row[1] or 0) for row in rows}

    sessions.set_collector(_collect_active)
    sandboxes.set_collector(_collect_sandboxes)
    return reg
