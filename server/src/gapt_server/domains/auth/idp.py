"""Identity providers + magic-link flow.

`AuthIdp` is the small protocol the routers depend on. `MagicLinkIdp`
ships in M1-E1; OIDC/SAML variants can plug in later without touching
the router or the session store.

Magic-link delivery defaults to console output (the token is logged at
INFO so a dev sees it during local runs). Wiring an SMTP adapter is a
follow-up — guarded behind a `delivery` injection so call sites stay
the same.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Protocol

import structlog
from sqlalchemy import select

from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.auth.session import (
    InMemorySessionStore,
    InMemoryTokenStore,
    Session,
    SessionStore,
    TokenStore,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class MagicLinkDelivery(Protocol):
    async def deliver(self, *, email: str, callback_url: str) -> None: ...


class ConsoleDelivery:
    """Logs the magic-link URL at INFO. Useful for local dev — the user
    copy/pastes from the log. M0 default."""

    async def deliver(self, *, email: str, callback_url: str) -> None:
        logger.info("auth.magic_link.console_delivery", email=email, callback_url=callback_url)


class AuthIdp(Protocol):
    """Minimal contract the auth router talks to. Multi-provider
    deployments wrap a registry around this protocol."""

    async def request_login(self, *, email: str, base_url: str) -> None: ...

    async def consume_token(self, *, token: str, db: AsyncSession) -> Session: ...


class MagicLinkIdp:
    """Token-by-email IDP.

    - `request_login` mints a token, stores it in the `TokenStore` for
      `token_ttl_s`, then asks `delivery` to send it.
    - `consume_token` atomically takes the token (one-shot), creates a
      `User` row on first use, and produces a `Session`.
    - Single-user mode: the first user to log in becomes the OWNER of a
      pre-seeded `default` org (also created on first use).
    """

    def __init__(
        self,
        *,
        token_store: TokenStore,
        session_store: SessionStore,
        delivery: MagicLinkDelivery,
        token_ttl_s: float = 900.0,
        session_ttl_s: float = 60 * 60 * 24 * 7,
    ) -> None:
        self._tokens = token_store
        self._sessions = session_store
        self._delivery = delivery
        self._token_ttl = token_ttl_s
        self._session_ttl = session_ttl_s

    async def request_login(self, *, email: str, base_url: str) -> None:
        token = secrets.token_urlsafe(32)
        await self._tokens.put(token, email, self._token_ttl)
        callback_url = f"{base_url.rstrip('/')}/api/auth/magic-link/callback?token={token}"
        await self._delivery.deliver(email=email, callback_url=callback_url)
        logger.info(
            "auth.magic_link.requested",
            email=email,
            ttl_s=self._token_ttl,
        )

    async def consume_token(self, *, token: str, db: AsyncSession) -> Session:
        email = await self._tokens.take(token)
        if email is None:
            raise AuthError("invalid_or_expired_token")

        user = await _ensure_user(db, email=email)
        await db.commit()

        session_id = secrets.token_urlsafe(32)
        return await self._sessions.create(session_id, user.id, self._session_ttl)

    async def logout(self, session_id: str) -> None:
        await self._sessions.delete(session_id)


class AuthError(Exception):
    """Surfaces as 401/400 in routers; carries a stable code suffix."""


# ─────────────────────────────────────────────────────────────── helpers ──


async def _ensure_user(db: AsyncSession, *, email: str) -> models.User:
    """Idempotent fetch-or-create. First user is promoted to OWNER of a
    default org (also created on first use)."""
    existing = (
        await db.execute(select(models.User).where(models.User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    user_id = new_ulid()
    user = models.User(id=user_id, email=email)
    db.add(user)
    await db.flush()

    default_org = (
        await db.execute(select(models.Org).where(models.Org.slug == "default"))
    ).scalar_one_or_none()
    if default_org is None:
        default_org = models.Org(
            id=new_ulid(),
            slug="default",
            name="Default",
            owner_id=user.id,
        )
        db.add(default_org)
        await db.flush()
        logger.info("auth.bootstrap.default_org_created", user_id=user.id)

    db.add(
        models.OrgMembership(
            org_id=default_org.id,
            user_id=user.id,
            role=enums.Role.OWNER,
        )
    )
    await db.flush()
    logger.info("auth.user.created", user_id=user.id, email=email)
    return user


# Convenience factory for tests / single-process dev runs.
def build_memory_idp(
    *, token_ttl_s: float = 900.0, session_ttl_s: float = 60 * 60 * 24 * 7
) -> MagicLinkIdp:
    return MagicLinkIdp(
        token_store=InMemoryTokenStore(),
        session_store=InMemorySessionStore(),
        delivery=ConsoleDelivery(),
        token_ttl_s=token_ttl_s,
        session_ttl_s=session_ttl_s,
    )


__all__ = [
    "AuthError",
    "AuthIdp",
    "ConsoleDelivery",
    "MagicLinkDelivery",
    "MagicLinkIdp",
    "build_memory_idp",
]
