"""TOTP 2FA verification — protocol + dev stub.

The Orchestrator (`orchestrator.py`) consults `TwoFactorVerifier`
whenever the PolicyEngine returns `REQUIRE_2FA` for a deploy action.
The dev stub `AcceptAnyCodeVerifier` validates the *presence* of a
code but not its value — so we can wire the full flow end-to-end
today and slot in a real TOTP library + `users.totp_secret` migration
later without touching call sites.

Why a Protocol instead of a function: the verifier holds per-user
secrets, so a real implementation needs DB access. Keeping it
injectable also lets the future hardware-key path (WebAuthn) plug in
without an API break."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TwoFactorError(RuntimeError):
    """Stable code suffix surfaces to the router as HTTP 412."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TwoFactorVerifier(Protocol):
    """Verify a TOTP / WebAuthn / etc. code against a user."""

    async def verify(self, *, user_id: str, code: str | None) -> bool: ...


@dataclass(frozen=True)
class AcceptAnyCodeVerifier:
    """Dev stub: pass when *any non-empty* code is provided.
    Production swaps this for the TOTP-secret-backed verifier once
    `users.totp_secret_encrypted` ships. Tests use this so the
    deploy path is exercised end-to-end without a real secret."""

    name: str = "accept_any"

    async def verify(self, *, user_id: str, code: str | None) -> bool:
        return bool(code and code.strip())


@dataclass(frozen=True)
class AlwaysDenyVerifier:
    """Test helper — useful for asserting the 412 path explicitly."""

    name: str = "always_deny"

    async def verify(self, *, user_id: str, code: str | None) -> bool:
        return False
