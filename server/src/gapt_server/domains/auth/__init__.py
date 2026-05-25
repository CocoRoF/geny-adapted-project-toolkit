"""Auth domain — D2.

Single-admin auth (MinIO/Jenkins-style). The old multi-user magic-link
IDP + role hierarchy is gone — see `routers/auth.py` for the actual
endpoints and `principal.py` for the principal type.

Exposes:
- `AdminPrincipal` — what every authenticated request carries.
- `SessionStore` / `Session` — the cookie-keyed in-memory session
  store (Redis backend slot reserved for later).
- GitHub Device Flow helpers — kept because they're used by the per-
  project git push path, not by user login.
"""

from gapt_server.domains.auth.github_oauth import (
    DeviceFlowSession,
    GithubDeviceFlow,
    GithubOAuthError,
    IssuedToken,
    github_secret_key_name,
)
from gapt_server.domains.auth.principal import AdminPrincipal
from gapt_server.domains.auth.session import (
    InMemorySessionStore,
    Session,
    SessionStore,
)

__all__ = [
    "AdminPrincipal",
    "DeviceFlowSession",
    "GithubDeviceFlow",
    "GithubOAuthError",
    "InMemorySessionStore",
    "IssuedToken",
    "Session",
    "SessionStore",
    "github_secret_key_name",
]
