"""GitHub OAuth Device Flow — token issuance against the GitHub API.

Pure I/O on the GitHub side; the resulting access token is handed to
``SecretVault`` so the only plaintext copy is the encrypted blob on
disk + whatever lives momentarily in memory during a session boot.

The control plane reads tokens via `SecretVault.read` (always audited)
when it spawns a sandbox; the askpass helper inside the sandbox
receives them via short-lived env var so git/gh never touch
``/home/.../.git-credentials``.

See ``docs/05_git_workflow.md`` §5.2.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
import structlog

from gapt_server.db import enums

if TYPE_CHECKING:
    from collections.abc import Callable

# GitHub's documented endpoints. Overridable via the constructor for
# tests + GitHub Enterprise installations.
_DEFAULT_DEVICE_CODE_URL = "https://github.com/login/device/code"
_DEFAULT_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
_DEFAULT_REVOKE_URL = "https://api.github.com/applications/{client_id}/token"

# Scopes the GAPT agent needs to clone/push/PR against a private repo.
_DEFAULT_SCOPES = "repo,workflow"


logger = structlog.get_logger(__name__)


class GithubOAuthError(RuntimeError):
    """Stable code suffix for the API layer to translate to HTTP."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DeviceFlowSession:
    """What the user (and the polling loop) gets after `start`."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float  # epoch seconds
    interval_s: int


@dataclass(frozen=True)
class IssuedToken:
    """Result of a successful polling run."""

    access_token: str
    token_type: str
    scope: str


@dataclass
class GithubDeviceFlow:
    """Driver for GitHub's Device Authorization grant.

    Stateless: the caller stores the ``DeviceFlowSession`` (e.g. in
    Redis keyed by user_id) between ``start`` and ``poll``. We don't
    keep tokens in memory beyond the polling call.
    """

    client_id: str
    scopes: str = _DEFAULT_SCOPES
    device_code_url: str = _DEFAULT_DEVICE_CODE_URL
    access_token_url: str = _DEFAULT_ACCESS_TOKEN_URL
    revoke_url_template: str = _DEFAULT_REVOKE_URL
    client_factory: Callable[[], httpx.AsyncClient] | None = field(default=None, repr=False)
    timeout_s: float = 15.0

    def _client(self) -> httpx.AsyncClient:
        if self.client_factory is not None:
            return self.client_factory()
        return httpx.AsyncClient(timeout=self.timeout_s, headers={"Accept": "application/json"})

    async def start(self) -> DeviceFlowSession:
        async with self._client() as client:
            try:
                response = await client.post(
                    self.device_code_url,
                    data={"client_id": self.client_id, "scope": self.scopes},
                )
            except httpx.HTTPError as exc:
                raise GithubOAuthError(
                    "auth.github.transport",
                    f"failed to reach GitHub device endpoint: {exc!s}",
                ) from exc
        if response.status_code >= 400:
            raise GithubOAuthError(
                "auth.github.transport",
                f"GitHub returned {response.status_code}: {response.text[:200]}",
            )
        body = response.json()
        if "device_code" not in body or "user_code" not in body:
            raise GithubOAuthError(
                "auth.github.malformed_response",
                f"device-code response missing required fields: {sorted(body)}",
            )
        expires_in = int(body.get("expires_in", 900))
        return DeviceFlowSession(
            device_code=body["device_code"],
            user_code=body["user_code"],
            verification_uri=body.get("verification_uri", "https://github.com/login/device"),
            expires_at=time.time() + expires_in,
            interval_s=int(body.get("interval", 5)),
        )

    async def poll_once(self, session: DeviceFlowSession) -> IssuedToken | None:
        """Single polling tick. Returns ``None`` if the user hasn't
        verified yet; raises ``GithubOAuthError`` on terminal states
        (denied / expired / slow_down beyond budget).
        """
        async with self._client() as client:
            try:
                response = await client.post(
                    self.access_token_url,
                    data={
                        "client_id": self.client_id,
                        "device_code": session.device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
            except httpx.HTTPError as exc:
                raise GithubOAuthError(
                    "auth.github.transport",
                    f"failed to reach access-token endpoint: {exc!s}",
                ) from exc

        if response.status_code >= 500:
            raise GithubOAuthError(
                "auth.github.transport",
                f"GitHub access-token returned {response.status_code}",
            )
        body = response.json()
        if "access_token" in body:
            return IssuedToken(
                access_token=body["access_token"],
                token_type=body.get("token_type", "bearer"),
                scope=body.get("scope", self.scopes),
            )

        # GitHub uses `error` field for the still-polling cases per RFC 8628.
        error = body.get("error", "")
        if error in {"authorization_pending", "slow_down"}:
            return None
        if error == "expired_token":
            raise GithubOAuthError(
                "auth.github.device_code_expired",
                "user did not complete the device flow before the code expired",
            )
        if error == "access_denied":
            raise GithubOAuthError(
                "auth.github.denied",
                "user denied the GitHub OAuth request",
            )
        raise GithubOAuthError(
            "auth.github.unknown",
            f"unexpected GitHub access-token response: {body}",
        )

    async def poll_until_complete(
        self,
        session: DeviceFlowSession,
        *,
        sleep: Callable[[float], asyncio.Future[None]] | None = None,
    ) -> IssuedToken:
        """Block until the user finishes (or the code expires). Honours
        the ``interval`` returned by GitHub and never polls more often
        than that — slow_down responses are handled by ``poll_once``.

        Tests inject ``sleep`` to skip wall-clock delays.
        """
        actual_sleep = sleep or asyncio.sleep
        while True:
            if time.time() >= session.expires_at:
                raise GithubOAuthError(
                    "auth.github.device_code_expired",
                    "user did not complete the device flow before the code expired",
                )
            issued = await self.poll_once(session)
            if issued is not None:
                return issued
            await actual_sleep(session.interval_s)

    async def revoke(self, *, token: str, client_secret: str) -> None:
        url = self.revoke_url_template.format(client_id=self.client_id)
        auth = (self.client_id, client_secret)
        async with self._client() as client:
            try:
                response = await client.request(
                    "DELETE",
                    url,
                    auth=auth,
                    json={"access_token": token},
                )
            except httpx.HTTPError as exc:
                raise GithubOAuthError(
                    "auth.github.transport",
                    f"revoke failed: {exc!s}",
                ) from exc
        if response.status_code not in {204, 404}:
            raise GithubOAuthError(
                "auth.github.transport",
                f"revoke returned {response.status_code}: {response.text[:200]}",
            )


# ─────────────────────────────────────────────────────────── helpers ──


def github_secret_key_name(user_id: str) -> str:
    """Stable key name used for storing the admin's GitHub token in
    `SecretVault` (scope=SYSTEM)."""
    return f"github_oauth_token::{user_id}"


def secret_owner_scope() -> enums.SecretOwnerScope:
    return enums.SecretOwnerScope.SYSTEM
