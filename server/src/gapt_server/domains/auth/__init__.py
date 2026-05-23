"""Auth domain — D2.

Exposes:
- `AuthIdp` protocol — pluggable identity provider (M1-E1 ships
  `MagicLinkIdp`; OIDC/SAML can land later without touching callers).
- `SessionStore` / `TokenStore` protocols + in-memory implementations
  (Redis-backed variants will land alongside the Redis dep wire-up).
- `MagicLinkIdp` — token-by-email flow with console fallback delivery.
- `get_current_user` dependency.
"""

from gapt_server.domains.auth.github_oauth import (
    DeviceFlowSession,
    GithubDeviceFlow,
    GithubOAuthError,
    IssuedToken,
    github_secret_key_name,
)
from gapt_server.domains.auth.idp import AuthIdp, MagicLinkIdp
from gapt_server.domains.auth.session import (
    InMemorySessionStore,
    InMemoryTokenStore,
    Session,
    SessionStore,
    TokenStore,
)

__all__ = [
    "AuthIdp",
    "DeviceFlowSession",
    "GithubDeviceFlow",
    "GithubOAuthError",
    "InMemorySessionStore",
    "InMemoryTokenStore",
    "IssuedToken",
    "MagicLinkIdp",
    "Session",
    "SessionStore",
    "TokenStore",
    "github_secret_key_name",
]
