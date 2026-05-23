"""Prometheus scrape endpoint.

Refreshes any registered async collector before rendering so live
gauges (sessions_active, sandbox_count) reflect the current DB state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from gapt_server.container import AppContainer, get_container
from gapt_server.observability.render import render_prometheus

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(
    container: AppContainer = Depends(get_container),  # noqa: B008
) -> PlainTextResponse:
    await container.registry.refresh_collectors()
    body = render_prometheus(container.registry)
    # Prometheus text format 0.0.4 — the version label keeps stricter
    # scrapers happy.
    return PlainTextResponse(
        body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
