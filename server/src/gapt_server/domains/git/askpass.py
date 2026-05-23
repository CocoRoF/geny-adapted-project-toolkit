"""Short-lived askpass token store — host token never lands on disk.

How the dance works:
1. Control plane reads the user's GitHub token from `SecretVault`.
2. It mints a 32-byte random ``AskpassToken`` (TTL 30s by default) and
   puts it in this store keyed by token id.
3. It spawns a sandbox-side git/gh command via the daemon's ``/exec``
   with two env vars set:

   - ``GIT_ASKPASS=/usr/local/bin/gapt-askpass`` (script bundled in
     the runtime image; `runtime/scripts/gapt-askpass.sh`)
   - ``GAPT_ASKPASS_TOKEN_ID=<token_id>``

4. The askpass helper inside the sandbox calls the daemon's
   ``/askpass/exchange`` endpoint, which forwards to the control plane's
   ``AskpassTokenStore.exchange`` and gets back the underlying token.
5. The helper prints the token to stdout; git/gh consume it once.
6. The token is single-use: subsequent ``exchange`` calls return None.

In M1-E2 Cycle 2.6a this module ships the in-process store + value
types. The daemon ``/askpass/exchange`` endpoint, the actual askpass
shell script in the runtime image, and the control-plane wire-up land
in Cycle 2.6b.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass


class AskpassError(RuntimeError):
    """Stable code suffix:
    - ``auth.askpass.expired``     — TTL elapsed before exchange.
    - ``auth.askpass.unknown``     — token id never seen.
    - ``auth.askpass.consumed``    — already exchanged once.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AskpassToken:
    token_id: str  # random opaque id sent into sandbox via env
    secret: str  # the actual git/gh token plaintext
    issued_at: float
    expires_at: float


@dataclass
class _Entry:
    secret: str
    expires_at: float
    consumed: bool = False


class AskpassTokenStore:
    """In-process token store. Single-process control plane uses this
    directly; a multi-node deployment swaps in a Redis-backed variant
    behind the same interface (M2)."""

    def __init__(self, *, default_ttl_s: float = 30.0) -> None:
        self._entries: dict[str, _Entry] = {}
        self._default_ttl = default_ttl_s
        self._lock = asyncio.Lock()

    async def issue(self, *, secret: str, ttl_s: float | None = None) -> AskpassToken:
        if not secret:
            raise AskpassError(
                "auth.askpass.unknown",
                "refusing to issue an askpass token for empty secret",
            )
        token_id = secrets.token_urlsafe(32)
        now = time.time()
        ttl = ttl_s if ttl_s is not None else self._default_ttl
        async with self._lock:
            self._entries[token_id] = _Entry(secret=secret, expires_at=now + ttl)
        return AskpassToken(token_id=token_id, secret=secret, issued_at=now, expires_at=now + ttl)

    async def exchange(self, token_id: str) -> str:
        """Atomic consume. Returns the secret on success; raises on
        any of the 3 failure modes."""
        async with self._lock:
            entry = self._entries.get(token_id)
            if entry is None:
                raise AskpassError(
                    "auth.askpass.unknown",
                    f"askpass token {token_id[:8]}… not recognised",
                )
            if entry.consumed:
                raise AskpassError(
                    "auth.askpass.consumed",
                    f"askpass token {token_id[:8]}… already exchanged",
                )
            if entry.expires_at < time.time():
                raise AskpassError(
                    "auth.askpass.expired",
                    f"askpass token {token_id[:8]}… expired",
                )
            entry.consumed = True
            return entry.secret

    async def revoke(self, token_id: str) -> None:
        async with self._lock:
            self._entries.pop(token_id, None)

    async def gc(self) -> int:
        """Drop expired / consumed entries. Returns the count removed.
        Callers run this periodically (Cycle 2.8b's ARQ scheduler)."""
        now = time.time()
        async with self._lock:
            doomed = [
                tid
                for tid, entry in self._entries.items()
                if entry.consumed or entry.expires_at < now
            ]
            for tid in doomed:
                del self._entries[tid]
        return len(doomed)
