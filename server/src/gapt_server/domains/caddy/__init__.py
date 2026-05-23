"""Caddy admin-API integration — dynamic subdomain registration."""

from gapt_server.domains.caddy.admin_api import (
    CaddyAdminClient,
    CaddyAdminError,
    CaddyHttpTransport,
)
from gapt_server.domains.caddy.share import (
    ShareLinkError,
    issue_share_link,
    parse_share_link,
)
from gapt_server.domains.caddy.subdomain import (
    SubdomainBinding,
    SubdomainManager,
)

__all__ = [
    "CaddyAdminClient",
    "CaddyAdminError",
    "CaddyHttpTransport",
    "ShareLinkError",
    "SubdomainBinding",
    "SubdomainManager",
    "issue_share_link",
    "parse_share_link",
]
