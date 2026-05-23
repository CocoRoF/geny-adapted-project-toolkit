"""JWT middleware for the toolkit-agent daemon.

The control plane mints a short-lived JWT (`GAPT_DAEMON_TOKEN` is the
HMAC secret) and includes it in every API call via the standard
``Authorization: Bearer <token>`` header. The daemon verifies:

- the signature against the configured secret (HS256),
- the ``aud`` claim equals ``gapt-runtime``,
- the ``iss`` claim equals ``gapt-server``,
- ``exp`` is in the future,
- the ``sub`` claim matches the session id baked into env (when set).

The /health endpoint is excluded so the host-side healthcheck can ping
without a token. Everything else requires a valid bearer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import jwt
import structlog
from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from gapt_runtime.settings import DaemonSettings

logger = structlog.get_logger(__name__)

EXEMPT_PATHS: frozenset[str] = frozenset({"/health"})
AUDIENCE = "gapt-runtime"
ISSUER = "gapt-server"


@web.middleware
async def jwt_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    if request.path in EXEMPT_PATHS:
        return await handler(request)

    from gapt_runtime.daemon import SETTINGS_KEY  # noqa: PLC0415

    settings: DaemonSettings = request.app[SETTINGS_KEY]
    if not settings.jwt_secret:
        # No secret configured = explicit dev mode. Refuse rather than
        # silently accept anything.
        raise web.HTTPInternalServerError(
            reason="daemon JWT secret is not configured (GAPT_DAEMON_TOKEN)"
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise web.HTTPUnauthorized(reason="missing Bearer token")
    token = auth_header[len("Bearer ") :].strip()
    if not token:
        raise web.HTTPUnauthorized(reason="empty Bearer token")

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise web.HTTPUnauthorized(reason="token expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise web.HTTPUnauthorized(reason="invalid audience") from exc
    except jwt.InvalidIssuerError as exc:
        raise web.HTTPUnauthorized(reason="invalid issuer") from exc
    except jwt.InvalidTokenError as exc:
        raise web.HTTPUnauthorized(reason="invalid token") from exc

    if settings.session_id is not None and payload.get("sub") != settings.session_id:
        raise web.HTTPUnauthorized(reason="session mismatch")

    request["jwt_payload"] = payload
    return await handler(request)
