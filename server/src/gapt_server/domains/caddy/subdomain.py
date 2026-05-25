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


def _path_route_payloads(binding: SubdomainBinding) -> list[dict[str, Any]]:
    """Two Caddy routes per path-mode preview binding.

    **Primary route** — matches `/preview/<slug>` and `/preview/<slug>/*`
    and reverse-proxies. When `binding.strip_prefix` is True (opt-in),
    `rewrite.strip_path_prefix` runs first so the upstream sees `/`.
    Default keeps the prefix so basePath-aware apps (Next.js basePath,
    Vite base, FastAPI root_path) round-trip cleanly.

    **Referer fallback** — Next.js (and many other frameworks) emit
    plain `<link rel="icon" href="/favicon.png">` and similar
    root-relative URLs that bypass basePath entirely. The browser
    requests those from the apex (`gapt.hrletsgo.me/favicon.png`)
    where the IDE catch-all would catch them. This second route
    matches any request whose `Referer` header starts with
    `/preview/<slug>` and reverse-proxies to the same upstream, so
    those stragglers land on the right app. The strip-prefix toggle
    doesn't apply here — Referer-borne requests are already at root.

    The primary route is registered first so prefixed requests never
    need the Referer check. Both carry the same upstream and a
    terminal flag.

    Both routes share a stable `@id` family (main + `-asset`) so the
    upsert / unregister flow can target them by id."""
    slug = binding.workspace_slug.lower()
    prefix = f"/preview/{slug}"
    upstream_handler: dict[str, Any] = {
        "handler": "reverse_proxy",
        "upstreams": [
            {"dial": f"{binding.upstream_host}:{binding.upstream_port}"}
        ],
    }
    primary_handlers: list[dict[str, Any]] = []
    if binding.strip_prefix:
        primary_handlers.append(
            {"handler": "rewrite", "strip_path_prefix": prefix}
        )
    primary_handlers.append(upstream_handler)
    primary = {
        "@id": _route_id(binding.workspace_slug),
        "match": [{"path": [prefix, f"{prefix}/*"]}],
        "handle": primary_handlers,
        "terminal": True,
    }
    # Referer fallback: catch root-relative asset requests whose
    # Referer originates from this preview. The right handling
    # depends on whether the primary route was stripping the
    # prefix:
    #
    #   * `strip_prefix=False` (prod with basePath baked): the
    #     upstream only knows URLs under `/preview/<slug>/...`, so
    #     a bare `/favicon.png` has to be rewritten WITH the prefix
    #     before forwarding.
    #
    #   * `strip_prefix=True` (dev servers without basePath): the
    #     upstream serves at root, so `/favicon.png` should be
    #     forwarded *as-is*. Re-adding the prefix here would just
    #     produce 404s — exactly the dev-mode bug we hit.
    #
    # Either way the Referer regex is the same; only the rewrite
    # differs.
    fallback_handlers: list[dict[str, Any]] = []
    if not binding.strip_prefix:
        fallback_handlers.append(
            {"handler": "rewrite", "uri": prefix + "{http.request.uri}"}
        )
    fallback_handlers.append(upstream_handler)
    asset_fallback = {
        "@id": _route_id(binding.workspace_slug) + "-asset",
        "match": [
            {
                "header_regexp": {
                    "Referer": {
                        "pattern": f"://[^/]+{prefix}(/|$|\\?)",
                    }
                }
            }
        ],
        "handle": fallback_handlers,
        "terminal": True,
    }
    return [primary, asset_fallback]


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
        """Splice the route(s) for this binding ahead of the IDE
        catch-all in the main server's `routes` array. Returns the
        user-visible URL host:
            path mode      → `<apex>/preview/<slug>`
            subdomain mode → `<slug>.<preview-domain>`

        Path mode registers TWO routes — the primary `/preview/<slug>`
        match plus a Referer-based fallback for root-relative assets
        (`<link rel="icon" href="/favicon.png">` etc.) that bypass the
        framework's basePath. See `_path_route_payloads` for details."""
        if binding.mode == PreviewMode.SUBDOMAIN:
            await self._upsert([_subdomain_route_payload(binding, self.preview_domain)])
            return _full_host(binding.workspace_slug, self.preview_domain)

        # Path mode — preview_domain is the apex (no subdomain).
        await self._upsert(_path_route_payloads(binding))
        host = self.preview_domain.rstrip(".").lower()
        return f"{host}/preview/{binding.workspace_slug.lower()}"

    async def _upsert(self, payloads: list[dict[str, Any]]) -> None:
        """Replace any pre-existing routes with matching `@id`s, then
        splice the fresh route(s) in *before* the first anchor route
        that would otherwise swallow the request (IDE catch-all OR
        the `/preview/*` safety-net 404 — see the insertion logic).

        Caddy's admin semantics aren't REST-vanilla: POST on an array
        element merges/appends; PUT on an existing path returns 409
        (`key already exists`). The only way to atomically replace an
        array is **DELETE then POST**. The lock guards that two-step
        sequence so two simultaneous registers can't observe the
        intermediate empty state.

        Multiple payloads land contiguously at the chosen insert
        point in the order given — important when one route's match
        is a superset of another (e.g. Referer-fallback should sit
        after the primary path match for predictable ordering)."""
        route_ids = {p["@id"] for p in payloads}
        async with self._lock:
            try:
                current = await self.client.get(self.routes_path)
            except CaddyAdminError:
                current = None
            arr: list[dict[str, Any]] = current if isinstance(current, list) else []
            # Drop prior routes sharing any of the fresh @ids.
            arr = [
                r for r in arr
                if not (isinstance(r, dict) and r.get("@id") in route_ids)
            ]
            # Find the FIRST anchor we must splice in front of. Caddy
            # evaluates routes in array order, so anything we want to
            # win must sit *earlier* than the routes that would
            # otherwise swallow the request:
            #
            #   1. The IDE catch-all — no `match`, a "real" handler
            #      (reverse_proxy / file_server / etc). Skipping past
            #      the leading `encode` block (handler=`encode`) is the
            #      reason for the handler-set check.
            #
            #   2. The `/preview/*` safety-net 404 (handler=
            #      `static_response`, match has any `/preview*` path).
            #      Added to Caddyfile.dev to keep unregistered slugs
            #      from falling through to the IDE SPA (which
            #      previously caused an iframe-in-iframe recursion bug
            #      where the GAPT IDE rendered inside the preview
            #      iframe). Without this branch the dynamic
            #      `/preview/<slug>` routes would be inserted AFTER
            #      the 404 and the 404 would always win.
            insert_at = len(arr)
            for i, r in enumerate(arr):
                handlers = [h.get("handler") for h in r.get("handle", [])]
                paths = (r.get("match") or [{}])[0].get("path") or []
                is_preview_safety_net = any(
                    isinstance(p, str) and p.startswith("/preview") for p in paths
                ) and "static_response" in handlers
                is_ide_catchall = (not r.get("match")) and any(
                    h in {"subroute", "reverse_proxy", "file_server"}
                    for h in handlers
                )
                if is_preview_safety_net or is_ide_catchall:
                    insert_at = i
                    break
            for offset, payload in enumerate(payloads):
                arr.insert(insert_at + offset, payload)
            # DELETE then POST — see method docstring.
            try:
                await self.client.delete(self.routes_path)
            except CaddyAdminError:
                # If the path was already empty, the GET above returned
                # None and the DELETE 404s; either way safe to proceed.
                pass
            await self.client.post(self.routes_path, arr)

    async def unregister(self, workspace_slug: str) -> None:
        """Delete both the primary preview route and its Referer
        fallback (if registered). Idempotent — 404 means "already
        gone" and is swallowed."""
        primary = _route_id(workspace_slug)
        for route_id in (primary, f"{primary}-asset"):
            try:
                await self.client.delete(f"/id/{route_id}")
            except CaddyAdminError as exc:
                if "404" in str(exc):
                    continue
                raise

    async def list_routes(self) -> list[dict[str, Any]]:
        body = await self.client.get(self.routes_path)
        if not isinstance(body, list):
            return []
        return body
