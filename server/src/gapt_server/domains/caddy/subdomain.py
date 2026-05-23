"""Per-workspace subdomain manager.

Each workspace gets a routed `{slug}.{preview_domain}` that proxies
to the workspace's *in-sandbox* HTTP service (typically the dev
server the user spun up). We update Caddy's routes via the admin
API without touching the static Caddyfile so adds / removes happen
in milliseconds.

The Caddy config slice we touch is the `routes` array of the first
server in `apps.http.servers.preview.routes`. We assume the operator
has bootstrapped Caddy with a server named `preview` listening on
443 + an on-demand TLS issuer; the manager only adds / removes
routes.

Concurrency: Caddy admin updates are atomic per request, but a
race between two simultaneous registrations would still let either
win. We accept that — the dockview shell doesn't expose multiple
workspace creations to the same user simultaneously, and the
underlying truth is the workspace row anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gapt_server.domains.caddy.admin_api import CaddyAdminClient, CaddyAdminError


@dataclass(frozen=True)
class SubdomainBinding:
    """The minimum the manager needs to register a route."""

    workspace_slug: str
    upstream_host: str  # e.g. "10.42.0.7"
    upstream_port: int  # the dev server port inside the sandbox


def _full_host(workspace_slug: str, preview_domain: str) -> str:
    """`{slug}.{domain}` — lowercased, trailing-dot-trimmed."""
    return f"{workspace_slug.lower()}.{preview_domain.rstrip('.').lower()}"


def _route_id(workspace_slug: str) -> str:
    """Stable Caddy route id — the workspace slug becomes the route's
    `@id` so we can DELETE by id instead of array index."""
    return f"gapt-workspace-{workspace_slug.lower()}"


def _route_payload(binding: SubdomainBinding, preview_domain: str) -> dict[str, Any]:
    """Build the Caddy route JSON for one workspace.

    `@id` is Caddy's convention for addressable config fragments;
    we use it so DELETE `/id/{route_id}` targets exactly one node.
    `host` matcher constrains the route to the workspace's
    subdomain — Caddy's on-demand TLS issues a cert lazily."""
    host = _full_host(binding.workspace_slug, preview_domain)
    return {
        "@id": _route_id(binding.workspace_slug),
        "match": [{"host": [host]}],
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [
                    {"dial": f"{binding.upstream_host}:{binding.upstream_port}"}
                ],
            }
        ],
        "terminal": True,
    }


@dataclass
class SubdomainManager:
    """Register / unregister workspace subdomains via Caddy admin."""

    client: CaddyAdminClient
    preview_domain: str
    routes_path: str = "/config/apps/http/servers/preview/routes"

    async def register(self, binding: SubdomainBinding) -> str:
        """POST a fresh route onto the preview server's `routes`
        array. Returns the resolved host (`{slug}.{domain}`)."""
        host = _full_host(binding.workspace_slug, self.preview_domain)
        payload = _route_payload(binding, self.preview_domain)
        await self.client.post(f"{self.routes_path}/...", payload)
        return host

    async def unregister(self, workspace_slug: str) -> None:
        """DELETE the addressable route by its `@id`."""
        route_id = _route_id(workspace_slug)
        try:
            await self.client.delete(f"/id/{route_id}")
        except CaddyAdminError as exc:
            if "404" in str(exc):
                return
            raise

    async def list_routes(self) -> list[dict[str, Any]]:
        """Read the current routes array (best-effort; returns empty
        list on 404 / unreachable)."""
        body = await self.client.get(self.routes_path)
        if not isinstance(body, list):
            return []
        return body
