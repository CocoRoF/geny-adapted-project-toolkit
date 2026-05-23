"""Session + token storage abstractions.

`TokenStore` is the short-lived (15 min) store for magic-link tokens.
`SessionStore` is the post-login session table keyed by an opaque
session-id cookie value. Both have an in-memory implementation for
tests / single-process dev runs; Redis-backed versions plug in later
without touching call sites.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: str
    issued_at: float
    expires_at: float


class TokenStore(Protocol):
    async def put(self, token: str, payload: str, ttl_s: float) -> None: ...

    async def take(self, token: str) -> str | None:
        """Atomic consume — returns None if absent/expired."""


class SessionStore(Protocol):
    async def create(self, session_id: str, user_id: str, ttl_s: float) -> Session: ...

    async def get(self, session_id: str) -> Session | None: ...

    async def delete(self, session_id: str) -> None: ...


# ─────────────────────────────────────────────────────── in-memory backends ──


class InMemoryTokenStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def put(self, token: str, payload: str, ttl_s: float) -> None:
        async with self._lock:
            self._items[token] = (payload, time.time() + ttl_s)

    async def take(self, token: str) -> str | None:
        async with self._lock:
            entry = self._items.pop(token, None)
            if entry is None:
                return None
            payload, expires_at = entry
            if expires_at < time.time():
                return None
            return payload


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str, user_id: str, ttl_s: float) -> Session:
        now = time.time()
        session = Session(
            session_id=session_id,
            user_id=user_id,
            issued_at=now,
            expires_at=now + ttl_s,
        )
        async with self._lock:
            self._sessions[session_id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.expires_at < time.time():
                self._sessions.pop(session_id, None)
                return None
            return session

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)
