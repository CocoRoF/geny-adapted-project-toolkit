"""Session storage abstraction.

`SessionStore` is the post-login session table keyed by an opaque
session-id cookie value. The in-memory implementation is the only
thing M1 ships — a solo self-hosted tool doesn't justify Redis. If
the server restarts, the operator re-logs in. The protocol is here
so a Redis-backed implementation can plug in later without touching
call sites.
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


class SessionStore(Protocol):
    async def create(self, session_id: str, user_id: str, ttl_s: float) -> Session: ...

    async def get(self, session_id: str) -> Session | None: ...

    async def delete(self, session_id: str) -> None: ...


# ─────────────────────────────────────────────────────── in-memory backend ──


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
