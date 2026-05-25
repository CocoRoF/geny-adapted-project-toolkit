"""Per-workspace preview route manager.

Each exposed workspace service (dev) or prod compose stack gets a
Caddy route injected via the admin API. Two routing strategies are
supported, picked per binding:

  * **Path** (default) — single apex domain, route by URL path:
        gapt.hrletsgo.me/preview/<slug>/*  →  upstream
    Caddy strips the `/preview/<slug>` prefix before forwarding so
    the upstream app sees its own root. This is the default because
    operators only need one DNS record + one TLS cert.

  * **Subdomain** — wildcard subdomain, route by Host header:
        <slug>.<preview-domain>/*  →  upstream
    Used when the upstream app generates absolute root-relative URLs
    (Next.js without basePath, etc.) and can't be moved off `/`. Set
    `mode="subdomain"` per binding; requires wildcard DNS + on-demand
    TLS at Caddy.

The Caddy server name is fixed to `main` (see Caddyfile.dev's
`servers { name main }` block); the admin API mounts every route
under `/config/apps/http/servers/main/routes/...`. Routes carry a
stable `@id` derived from the slug so we can DELETE by id at
unexpose time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from gapt_server.domains.caddy.admin_api import CaddyAdminClient, CaddyAdminError


class PreviewMode(StrEnum):
    PATH = "path"
    SUBDOMAIN = "subdomain"


@dataclass(frozen=True)
class SubdomainBinding:
    """The minimum the manager needs to register a route.

    `workspace_slug` is the URL-safe identifier the route is keyed
    by — for path mode it appears in the URL as `/preview/<slug>/*`,
    for subdomain mode it becomes `<slug>.<preview-domain>`.

    `strip_prefix` (path mode only): when True, Caddy removes the
    `/preview/<slug>` prefix before forwarding so the upstream sees
    its own root. Use this for apps that hard-bind to root URLs and
    can't be told their basePath at build time. Default is False —
    the canonical pattern (Next.js basePath, Vite base, FastAPI
    root_path, ...) is that the app *itself* knows it's mounted at
    a prefix and emits its own URLs accordingly. Stripping the
    prefix then would make the app's self-issued URLs collide with
    the apex IDE routes."""

    workspace_slug: str
    upstream_host: str  # docker DNS name on gapt-net, e.g. "gapt-ws-01k..."
    upstream_port: int  # listening port inside the container
    mode: PreviewMode = PreviewMode.PATH
    strip_prefix: bool = False


def _full_host(workspace_slug: str, preview_domain: str) -> str:
    """`{slug}.{domain}` — lowercased, trailing-dot-trimmed."""
    return f"{workspace_slug.lower()}.{preview_domain.rstrip('.').lower()}"


def _route_id(workspace_slug: str) -> str:
    """Stable Caddy route id — slug becomes the route's `@id` so we
    can DELETE by id instead of array index."""
    return f"gapt-preview-{workspace_slug.lower()}"


def _path_route_payload(binding: SubdomainBinding) -> dict[str, Any]:
    """Caddy route JSON for path-mode previews.

    Matches `/preview/<slug>` and `/preview/<slug>/*`, then
    reverse-proxies. When `binding.strip_prefix` is True (the
    opt-in), a `rewrite.strip_path_prefix` runs first so the upstream
    sees `/...`. Default keeps the prefix in the forwarded URL — that
    matches the canonical "app knows its basePath" pattern (Next.js
    basePath, Vite base, FastAPI root_path) and lets the app emit
    self-referential URLs that round-trip through the same route.

    Both the bare path and the trailing-slash form match — without
    that, hitting `/preview/foo` (no slash) would fall through to the
    IDE catch-all below it."""
    slug = binding.workspace_slug.lower()
    prefix = f"/preview/{slug}"
    handlers: list[dict[str, Any]] = []
    if binding.strip_prefix:
        handlers.append(
            {"handler": "rewrite", "strip_path_prefix": prefix}
        )
    handlers.append(
        {
            "handler": "reverse_proxy",
            "upstreams": [
                {"dial": f"{binding.upstream_host}:{binding.upstream_port}"}
            ],
        }
    )
    return {
        "@id": _route_id(binding.workspace_slug),
        "match": [{"path": [prefix, f"{prefix}/*"]}],
        "handle": handlers,
        "terminal": True,
    }


def _subdomain_route_payload(
    binding: SubdomainBinding, preview_domain: str
) -> dict[str, Any]:
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
    """Register / unregister preview routes via Caddy admin.

    Name kept for back-compat (callers still construct
    `SubdomainManager(...)`); the manager handles both path-mode and
    subdomain-mode bindings now."""

    client: CaddyAdminClient
    preview_domain: str
    # Server name in the Caddyfile (`servers { name main }`). The
    # admin API mounts all per-slug routes under this.
    server_name: str = "main"
    # When True, register() returns a URL using the apex domain
    # (`https://gapt.hrletsgo.me/preview/<slug>`) regardless of mode.
    # When False, subdomain mode returns `<slug>.<preview_domain>`.
    use_apex_for_path: bool = True
    # Serialises register/unregister so the DELETE-then-POST splice
    # cycle (see _upsert) never has two callers racing on the same
    # routes array.
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def routes_path(self) -> str:
        return f"/config/apps/http/servers/{self.server_name}/routes"

    async def register(self, binding: SubdomainBinding) -> str:
        """POST a fresh route onto the main server's routes. Returns
        the user-visible URL host (path mode: `<apex>/preview/<slug>`;
        subdomain mode: `<slug>.<preview-domain>`).

        The route is *prepended* so it matches before the IDE-fallback
        `handle {}` block in the Caddyfile — without that the IDE
        catches `/preview/<slug>` first."""
        if binding.mode == PreviewMode.SUBDOMAIN:
            payload = _subdomain_route_payload(binding, self.preview_domain)
            await self._upsert(payload)
            return _full_host(binding.workspace_slug, self.preview_domain)

        # Path mode — preview_domain is the apex (no subdomain).
        payload = _path_route_payload(binding)
        await self._upsert(payload)
        host = self.preview_domain.rstrip(".").lower()
        return f"{host}/preview/{binding.workspace_slug.lower()}"

    async def _upsert(self, payload: dict[str, Any]) -> None:
        """Replace any pre-existing route with the same `@id`, then
        splice the fresh route in *before* the IDE catch-all.

        Caddy's admin semantics aren't REST-vanilla: POST on an array
        element merges/appends; PUT on an existing path returns 409
        (`key already exists`). The only way to atomically replace an
        array is **DELETE then POST**. The lock guards that two-step
        sequence so two simultaneous registers can't observe the
        intermediate empty state.

        The IDE catch-all from `handle {}` in Caddyfile compiles to a
        no-match terminal subroute that would otherwise eat every
        request before our preview route runs. We splice in *before*
        it; the catch-all stays last."""
        route_id = payload["@id"]
        async with self._lock:
            try:
                current = await self.client.get(self.routes_path)
            except CaddyAdminError:
                current = None
            arr: list[dict[str, Any]] = current if isinstance(current, list) else []
            # Drop any prior route with the same @id (upsert semantics).
            arr = [r for r in arr if not (isinstance(r, dict) and r.get("@id") == route_id)]
            # Find the IDE catch-all (no `match`, terminal-ish handler
            # that responds). The leading `encode` block also has no
            # match but its handler is `encode` (a header filter), so
            # we skip past it.
            insert_at = len(arr)
            for i, r in enumerate(arr):
                if r.get("match"):
                    continue
                handlers = [h.get("handler") for h in r.get("handle", [])]
                if any(
                    h in {"subroute", "reverse_proxy", "static_response", "file_server"}
                    for h in handlers
                ):
                    insert_at = i
                    break
            arr.insert(insert_at, payload)
            # DELETE then POST — see method docstring.
            try:
                await self.client.delete(self.routes_path)
            except CaddyAdminError:
                # If the path was already empty, the GET above returned
                # None and the DELETE 404s; either way safe to proceed.
                pass
            await self.client.post(self.routes_path, arr)

    async def unregister(self, workspace_slug: str) -> None:
        route_id = _route_id(workspace_slug)
        try:
            await self.client.delete(f"/id/{route_id}")
        except CaddyAdminError as exc:
            if "404" in str(exc):
                return
            raise

    async def list_routes(self) -> list[dict[str, Any]]:
        body = await self.client.get(self.routes_path)
        if not isinstance(body, list):
            return []
        return body
