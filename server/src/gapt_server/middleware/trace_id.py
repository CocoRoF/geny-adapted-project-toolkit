"""Per-request trace-id binding for structured logs.

Reads/generates a request id, binds it onto `structlog.contextvars` so
every log line emitted while handling the request carries it, and
echoes the id back as a response header. Downstream callers
(daemon RPC, geny-executor pipeline, MCP bridge) can propagate the
same id by passing it via request headers / env vars.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import structlog
from starlette.middleware.base import BaseHTTPMiddleware

from gapt_server.db.ulid import new_ulid

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

DEFAULT_HEADER: Final[str] = "X-Request-Id"


class TraceIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, header_name: str = DEFAULT_HEADER) -> None:
        super().__init__(app)
        self._header = header_name

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(self._header)
        trace_id = incoming if incoming is not None and _is_well_formed(incoming) else new_ulid()

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
        )
        try:
            response = await call_next(request)
        finally:
            # Don't leak contextvars across requests in the same worker.
            structlog.contextvars.clear_contextvars()

        response.headers[self._header] = trace_id
        return response


def _is_well_formed(value: str) -> bool:
    """Accept any printable-ASCII string of length 8-80.

    Lets callers pass their own correlation IDs while rejecting clearly
    malformed inputs.
    """
    if not 8 <= len(value) <= 80:
        return False
    return all(32 <= ord(c) < 127 for c in value)
