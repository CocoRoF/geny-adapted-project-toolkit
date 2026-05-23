"""TraceIdMiddleware — every response carries a request id, structlog
contextvars are bound for the duration of the request."""

from __future__ import annotations

import json
import logging
import re
from io import StringIO
from typing import TYPE_CHECKING

import pytest
import structlog
from httpx import ASGITransport, AsyncClient

from gapt_server.app import create_app
from gapt_server.container import build_container
from gapt_server.settings import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _isolated_app() -> FastAPI:
    settings = Settings(postgres_dsn=None)
    container = build_container(settings)
    return create_app(settings=settings, container=container)


@pytest.mark.asyncio
async def test_response_carries_generated_trace_id() -> None:
    app = _isolated_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    request_id = response.headers["X-Request-Id"]
    assert ULID_RE.match(request_id), f"expected ULID, got: {request_id}"


@pytest.mark.asyncio
async def test_response_echoes_inbound_trace_id() -> None:
    app = _isolated_app()
    incoming = "test-correlation-123456"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-Id": incoming})
    assert response.headers["X-Request-Id"] == incoming


@pytest.mark.asyncio
async def test_malformed_inbound_id_replaced() -> None:
    app = _isolated_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 7-char string falls under the 8-char minimum guard.
        response = await client.get("/health", headers={"X-Request-Id": "abcdefg"})
    assert response.headers["X-Request-Id"] != "abcdefg"
    assert ULID_RE.match(response.headers["X-Request-Id"])


@pytest.mark.asyncio
async def test_trace_id_lands_in_structlog_output() -> None:
    """The bound trace_id appears in any log emitted while the request
    is in-flight. We attach a JSON sink to root logging, hit /health,
    and inspect the captured line."""

    # create_app() calls configure_logging() which re-configures structlog,
    # so set up our captured sink *after* the app is built.
    app = _isolated_app()

    buf = StringIO()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )

    log = structlog.get_logger("trace_id.test")

    @app.get("/__emit_log")
    async def emit_log() -> dict[str, str]:
        log.info("trace.probe", marker="hello")
        return {"ok": "true"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/__emit_log", headers={"X-Request-Id": "abcdefgh1234"})

    payload = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    probe = next(rec for rec in payload if rec.get("event") == "trace.probe")
    assert probe["trace_id"] == "abcdefgh1234"
    assert probe["method"] == "GET"
    assert probe["path"] == "/__emit_log"
    assert response.headers["X-Request-Id"] == "abcdefgh1234"
