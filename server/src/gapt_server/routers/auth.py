"""Magic-link auth routes.

- `POST /api/auth/magic-link` — accept email, mint+deliver a token.
- `GET  /api/auth/magic-link/callback` — consume token, set session cookie.
- `POST /api/auth/logout` — clear session.
- `GET  /api/auth/me` — return the authenticated user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from gapt_server.container import get_app_settings, get_db_session
from gapt_server.db import models
from gapt_server.domains.auth.idp import AuthError, MagicLinkIdp, build_memory_idp

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from gapt_server.domains.auth.session import Session
    from gapt_server.settings import Settings

logger = structlog.get_logger(__name__)


# A single IDP instance per process. M1-E1 ships the in-memory backend;
# Redis-backed token + session stores swap in here later without
# touching call sites.
_DEFAULT_IDP: MagicLinkIdp | None = None


def get_auth_idp() -> MagicLinkIdp:
    global _DEFAULT_IDP  # noqa: PLW0603 — module-level singleton is intentional
    if _DEFAULT_IDP is None:
        _DEFAULT_IDP = build_memory_idp()
    return _DEFAULT_IDP


def set_auth_idp(idp: MagicLinkIdp) -> None:
    """Test / startup hook. Replaces the module-level singleton."""
    global _DEFAULT_IDP  # noqa: PLW0603
    _DEFAULT_IDP = idp


router = APIRouter(prefix="/api/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkAccepted(BaseModel):
    status: str = "ok"
    message: str = "Magic link sent (or printed to server log in dev mode)."


class CallbackResponse(BaseModel):
    user_id: str
    email: str


@router.post(
    "/magic-link",
    response_model=MagicLinkAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    idp: MagicLinkIdp = Depends(get_auth_idp),  # noqa: B008
) -> MagicLinkAccepted:
    base_url = str(request.base_url).rstrip("/")
    await idp.request_login(email=payload.email, base_url=base_url)
    return MagicLinkAccepted()


@router.get("/magic-link/callback", response_model=CallbackResponse)
async def magic_link_callback(
    token: str,
    response: Response,
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
    idp: MagicLinkIdp = Depends(get_auth_idp),  # noqa: B008
) -> CallbackResponse:
    try:
        session = await idp.consume_token(token=token, db=db)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "auth.token.invalid", "reason": str(exc)},
        ) from exc

    user = (
        await db.execute(select(models.User).where(models.User.id == session.user_id))
    ).scalar_one()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session.session_id,
        max_age=int(session.expires_at - session.issued_at),
        httponly=True,
        secure=settings.env != "dev",
        samesite="lax",
    )
    logger.info("auth.session.issued", user_id=session.user_id)
    return CallbackResponse(user_id=user.id, email=user.email)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    idp: MagicLinkIdp = Depends(get_auth_idp),  # noqa: B008
) -> Response:
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        await idp.logout(cookie)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.session_cookie_name)
    return response


class MeResponse(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None


async def _resolve_current_session(
    request: Request,
    settings: Settings,
    idp: MagicLinkIdp,
) -> Session:
    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "auth.session.missing"},
        )
    session = await idp._sessions.get(cookie)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "auth.session.expired"},
        )
    return session


async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    idp: MagicLinkIdp = Depends(get_auth_idp),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> models.User:
    session = await _resolve_current_session(request, settings, idp)
    user = (
        await db.execute(select(models.User).where(models.User.id == session.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "auth.user.not_found"},
        )
    return user


@router.get("/me", response_model=MeResponse)
async def me(user: models.User = Depends(get_current_user)) -> MeResponse:  # noqa: B008
    return MeResponse(user_id=user.id, email=user.email, display_name=user.display_name)
