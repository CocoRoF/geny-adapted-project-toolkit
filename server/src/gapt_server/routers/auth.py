"""Single-admin auth routes.

GAPT is a self-hosted solo tool — there is no multi-user system, no
email/magic-link round-trip, no role hierarchy. The control plane
exposes a single admin identity configured via env vars:

  GAPT_ADMIN_ID         (default "admin")
  GAPT_ADMIN_PASSWORD   (default "admin")
  GAPT_AUTH_ENABLED     (default true; flip to false to skip login
                         entirely on trusted localhost deployments)

Endpoints:
  POST /_gapt/api/auth/login   {id, password} → 204 + session cookie
  POST /_gapt/api/auth/logout                  → 204 + cookie cleared
  GET  /_gapt/api/auth/me                      → admin principal
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from gapt_server.container import get_app_settings
from gapt_server.domains.auth.principal import AdminPrincipal
from gapt_server.domains.auth.session import InMemorySessionStore, SessionStore

if TYPE_CHECKING:
    from gapt_server.settings import Settings

logger = structlog.get_logger(__name__)


# 30 days — long enough that the user doesn't need to log in every
# few hours on their own machine. The cookie is httponly+samesite=lax
# so this is fine for a single-tenant self-hosted tool.
_SESSION_TTL_S = 30 * 24 * 3600
# Synthetic user id for sessions. Always equal to settings.admin_id
# at issue time so audit rows pick up the operator-configured name.

# Module-level singleton session store. Swap via `set_session_store`
# in tests. Sessions are in-memory only — losing them on restart is
# acceptable for a solo tool (just re-login).
_DEFAULT_STORE: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _DEFAULT_STORE  # noqa: PLW0603 — module-level singleton is intentional
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = InMemorySessionStore()
    return _DEFAULT_STORE


def set_session_store(store: SessionStore) -> None:
    """Test / startup hook. Replaces the module-level singleton."""
    global _DEFAULT_STORE  # noqa: PLW0603
    _DEFAULT_STORE = store


router = APIRouter(prefix="/_gapt/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    id: str
    password: str


class MeResponse(BaseModel):
    user_id: str
    display_name: str | None = None
    # Echoed back so the SPA can skip the login screen when the
    # operator has set GAPT_AUTH_ENABLED=false.
    auth_enabled: bool = True


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(
    payload: LoginRequest,
    response: Response,
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    # When auth is disabled the login endpoint still works (the SPA's
    # generic submit path doesn't change) but accepts anything — a
    # cookie is issued so subsequent requests look identical to the
    # enabled path.
    if settings.auth_enabled:
        if not (
            secrets.compare_digest(payload.id, settings.admin_id)
            and secrets.compare_digest(payload.password, settings.admin_password)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "auth.invalid_credentials", "reason": "id or password mismatch"},
            )

    session_id = secrets.token_urlsafe(32)
    await store.create(
        session_id=session_id,
        user_id=settings.admin_id,
        ttl_s=_SESSION_TTL_S,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=_SESSION_TTL_S,
        httponly=True,
        secure=settings.env != "dev",
        samesite="lax",
    )
    logger.info("auth.login.ok", admin_id=settings.admin_id)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        await store.delete(cookie)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.session_cookie_name)
    return response


def _request_from_sandbox_net(request: Request) -> bool:
    """True when the request originates from a workspace sandbox on
    gapt-net rather than the operator's browser.

    The control plane shares gapt-net with every sandbox (so Caddy can
    reach them), which means a prompt-injected agent has a network path
    to `/_gapt/api/*`. The session cookie normally blocks it — but with
    `auth_enabled=false` that gate is gone. We treat any forwarded
    client IP inside the docker bridge ranges (172.16/12, 10/8) as
    sandbox-origin so the "trusted localhost" shortcut never silently
    hands a sandbox full admin. Real operator traffic arrives via the
    Cloudflare tunnel / loopback and is not in these ranges."""
    import ipaddress  # noqa: PLC0415

    candidates: list[str] = []
    xff = request.headers.get("x-forwarded-for")
    if xff:
        candidates.append(xff.split(",")[0].strip())
    if request.client is not None:
        candidates.append(request.client.host)
    for raw in candidates:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if ip.is_loopback:
            return False  # operator via loopback / tunnel — trusted
        if ip in ipaddress.ip_network("172.16.0.0/12") or ip in ipaddress.ip_network("10.0.0.0/8"):
            return True
    return False


async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> AdminPrincipal:
    """The single admin principal. Returned unconditionally when auth
    is disabled; otherwise the session cookie must resolve to a live
    entry in the store."""
    if not settings.auth_enabled:
        # Defence-in-depth: even with auth off, never auto-admit a
        # request that came from a workspace sandbox on gapt-net. The
        # agent's only sanctioned API surface is the scoped-bearer MCP
        # under /_gapt/api/mcp (mounted separately, not via this dep).
        if _request_from_sandbox_net(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "auth.sandbox_origin_forbidden"},
            )
        return AdminPrincipal(id=settings.admin_id, display_name=settings.admin_id)
    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "auth.session.missing"},
        )
    session = await store.get(cookie)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "auth.session.expired"},
        )
    return AdminPrincipal(id=session.user_id, display_name=session.user_id)


@router.get("/me", response_model=MeResponse)
async def me(
    user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
) -> MeResponse:
    return MeResponse(
        user_id=user.id,
        display_name=user.display_name,
        auth_enabled=settings.auth_enabled,
    )
