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
    # ── upstream transport (the user's prod stack sometimes ships its
    # own reverse-proxy that expects HTTPS + a specific Host) ──
    upstream_scheme: str = "http"  # "http" | "https"
    # When set, Caddy rewrites the outgoing `Host:` header to this
    # value before forwarding. Useful when the upstream's nginx /
    # traefik / etc. only recognises a particular `server_name` and
    # we sit behind a different preview domain.
    upstream_host_header: str | None = None
    # When True, Caddy doesn't verify the upstream's TLS certificate.
    # Required for HTTPS upstreams that present a self-signed or
    # mismatched cert — the user's prod stack often falls in this
    # bucket since its cert is for the public domain, not the
    # internal docker DNS name.
    upstream_tls_insecure: bool = False
    # ── cookie pinning (path mode only) ──
    # Path mode shares the apex domain with GAPT itself. Since
    # `cfe55b1`'s `/_gapt/*` namespacing, root-relative URLs the
    # upstream app emits (`/api/foo`, `/_next/static/...`,
    # `/favicon.png`) NO LONGER hit GAPT routes — they 404 cleanly.
    # But in-iframe NAVIGATIONS (e.g. user clicks a link to
    # `/projects` inside the preview) still bounce to the apex root
    # where GAPT's 302 → /_gapt/app/ would whisk them off into the
    # IDE — confusing. The Referer + Cookie fallbacks below catch
    # those nav cases and steer them back into the preview.
    #
    # When this is > 0, Caddy sets a short-lived
    # `gapt_preview=<slug>` cookie when the visitor first hits
    # `/preview/<slug>/...`, and a separate route forwards any
    # later root-relative request bearing that cookie back to the
    # same upstream. TTL kept intentionally short (default 5 min)
    # to bound the "tab A's GAPT got eaten by tab B's preview"
    # cookie-pollution window; for true robustness use subdomain
    # mode instead. Set to 0 to disable.
    cookie_pinning_ttl_s: int = 300


def _full_host(workspace_slug: str, preview_domain: str) -> str:
    """`{slug}.{domain}` — lowercased, trailing-dot-trimmed."""
    return f"{workspace_slug.lower()}.{preview_domain.rstrip('.').lower()}"


def _route_id(workspace_slug: str) -> str:
    """Stable Caddy route id — slug becomes the route's `@id` so we
    can DELETE by id instead of array index."""
    return f"gapt-preview-{workspace_slug.lower()}"


def _reverse_proxy_handler(binding: SubdomainBinding) -> dict[str, Any]:
    """Build the Caddy `reverse_proxy` handler for a binding.

    Most workspaces just need an HTTP upstream by docker DNS name, but
    prod compose stacks sometimes front their services with their own
    nginx/traefik that:
      * only speaks HTTPS (forces 301 from :80),
      * presents a cert for the *public* domain rather than the docker
        DNS name (so verification would fail), and
      * uses `server_name` matching to route — meaning the inbound
        `Host:` header has to be the public domain, not ours.

    The optional `upstream_scheme` / `upstream_tls_insecure` /
    `upstream_host_header` knobs cover that case. Defaults stay
    backwards-compatible (plain HTTP, no rewrite)."""
    handler: dict[str, Any] = {
        "handler": "reverse_proxy",
        "upstreams": [{"dial": f"{binding.upstream_host}:{binding.upstream_port}"}],
    }
    if binding.upstream_scheme == "https":
        tls: dict[str, Any] = {}
        if binding.upstream_tls_insecure:
            tls["insecure_skip_verify"] = True
        handler["transport"] = {"protocol": "http", "tls": tls}
    if binding.upstream_host_header:
        handler["headers"] = {
            "request": {"set": {"Host": [binding.upstream_host_header]}}
        }
    return handler


def _path_route_payloads(binding: SubdomainBinding) -> list[dict[str, Any]]:
    """Up to three Caddy routes per path-mode preview binding.

    **Primary route** — matches `/preview/<slug>` and `/preview/<slug>/*`
    and reverse-proxies. When `binding.strip_prefix` is True (opt-in),
    `rewrite.strip_path_prefix` runs first so the upstream sees `/`.
    Default keeps the prefix so basePath-aware apps (Next.js basePath,
    Vite base, FastAPI root_path) round-trip cleanly. When cookie
    pinning is enabled, this route also sets a short-lived
    `gapt_preview=<slug>` cookie so later root-relative requests can
    be re-routed without a Referer.

    **Cookie fallback** — when cookie pinning is enabled, this matches
    any request bearing `Cookie: gapt_preview=<slug>` AND
    `Sec-Fetch-Site: same-origin` (so cross-site or top-frame typed
    navigations don't accidentally route — only same-origin sub-
    resource / in-iframe requests). Reverse-proxies to the same
    upstream. The Sec-Fetch-Site guard isn't perfect (clicking
    around GAPT in another tab is also same-origin), which is why
    the TTL is short and subdomain mode is recommended for true
    multi-tenant robustness.

    **Referer fallback** — Next.js (and many other frameworks) emit
    plain `<link rel="icon" href="/favicon.png">` and similar
    root-relative URLs that bypass basePath entirely. The browser
    requests those from the apex (`gapt.hrletsgo.me/favicon.png`)
    where the IDE catch-all would catch them. This route matches any
    request whose `Referer` header starts with `/preview/<slug>` and
    reverse-proxies. Kept as defense-in-depth alongside the cookie
    fallback: a cross-tab navigation may strip the cookie via TTL
    expiry but still carry a Referer (or vice-versa).

    All routes carry a stable `@id` family (main + `-asset` +
    `-cookie`) so the upsert / unregister flow can target them by
    id."""
    slug = binding.workspace_slug.lower()
    prefix = f"/preview/{slug}"
    upstream_handler = _reverse_proxy_handler(binding)
    primary_handlers: list[dict[str, Any]] = []
    if binding.cookie_pinning_ttl_s > 0:
        # Set-Cookie on the primary route: short TTL, Path=/ so the
        # cookie is sent for any root-relative follow-up, SameSite=Lax
        # so cross-site embedders don't carry it, HttpOnly so the
        # app's JS can't read it (we own the routing decision).
        ttl = binding.cookie_pinning_ttl_s
        cookie = (
            f"gapt_preview={slug}; Path=/; Max-Age={ttl}; "
            f"SameSite=Lax; HttpOnly"
        )
        primary_handlers.append(
            {
                "handler": "headers",
                "response": {"set": {"Set-Cookie": [cookie]}},
            }
        )
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
    payloads: list[dict[str, Any]] = [primary, asset_fallback]
    if binding.cookie_pinning_ttl_s > 0:
        # The cookie regex matches the slug as an exact value, anchored
        # by the standard cookie-pair delimiters (start-of-header or
        # `; ` before, `;` or end-of-header after). This avoids
        # `gapt_preview=a` accidentally matching when the actual
        # cookie is `gapt_preview=ab` — though in practice slugs are
        # ULIDs so a false collision is vanishingly unlikely.
        #
        # Sec-Fetch-Site=same-origin narrows the catchment to
        # navigations whose origin already is the apex: clicks from
        # inside the preview iframe (same-origin), in-tab nav from
        # `/preview/<slug>/...` to `/projects`, etc. It excludes
        # cross-site nav (`cross-site`) and top-level typed URLs
        # (`none`), which would otherwise hijack legitimate fresh
        # visits to the GAPT UI in another tab.
        cookie_fallback = {
            "@id": _route_id(binding.workspace_slug) + "-cookie",
            "match": [
                {
                    "header_regexp": {
                        "Cookie": {
                            "pattern": f"(?:^|;\\s*)gapt_preview={slug}(?:;|$)",
                        }
                    },
                    "header": {"Sec-Fetch-Site": ["same-origin"]},
                }
            ],
            "handle": fallback_handlers,
            "terminal": True,
        }
        payloads.append(cookie_fallback)
    return payloads


def _subdomain_route_payload(
    binding: SubdomainBinding, preview_domain: str
) -> dict[str, Any]:
    host = _full_host(binding.workspace_slug, preview_domain)
    return {
        "@id": _route_id(binding.workspace_slug),
        "match": [{"host": [host]}],
        "handle": [_reverse_proxy_handler(binding)],
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

        Path mode registers up to THREE routes — the primary
        `/preview/<slug>` match, a Referer-based fallback, and a
        cookie-pinning fallback for root-relative URLs that bypass
        the framework's basePath. See `_path_route_payloads` for
        details. Subdomain mode registers a single host-matched
        route; no fallbacks needed (host header is the routing
        key).

        Before splicing the fresh payloads, the full @id family for
        this slug (`gapt-preview-<slug>`, `-asset`, `-cookie`) is
        cleared from the routes array — so switching mode (e.g.
        path → subdomain) doesn't leave the old fallback routes
        orphaned."""
        # Stale @ids to clear regardless of which mode we're entering.
        # This is the cross-mode cleanup — without it a path-mode
        # binding upgraded to subdomain would leave its `-asset` /
        # `-cookie` fallback routes catching requests forever.
        primary_id = _route_id(binding.workspace_slug)
        full_family = {primary_id, f"{primary_id}-asset", f"{primary_id}-cookie"}

        if binding.mode == PreviewMode.SUBDOMAIN:
            await self._upsert(
                [_subdomain_route_payload(binding, self.preview_domain)],
                extra_clear=full_family,
            )
            return _full_host(binding.workspace_slug, self.preview_domain)

        # Path mode — preview_domain is the apex (no subdomain).
        await self._upsert(_path_route_payloads(binding), extra_clear=full_family)
        host = self.preview_domain.rstrip(".").lower()
        return f"{host}/preview/{binding.workspace_slug.lower()}"

    async def _upsert(
        self,
        payloads: list[dict[str, Any]],
        *,
        extra_clear: set[str] | None = None,
    ) -> None:
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
        if extra_clear:
            # Caller is asking us to clear extra @ids too — used to
            # drop the full @id family on a mode switch so stale
            # fallback routes from the previous mode don't linger.
            route_ids = route_ids | extra_clear
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
            # Caddyfile-authored blocks compile down to a `subroute`
            # outer handler with the *real* handler nested one level
            # in (e.g. `{handler: subroute, routes: [{handle: [{handler:
            # static_response, ...}]}]}`). The detection here flattens
            # outer + nested into a single set so the safety-net and
            # IDE catch-all are recognised regardless of whether they
            # were registered via the admin API or compiled from the
            # Caddyfile.
            def _all_handlers(route: dict[str, Any]) -> set[str]:
                names: set[str] = set()
                for h in route.get("handle", []) or []:
                    name = h.get("handler")
                    if name:
                        names.add(name)
                    if name == "subroute":
                        for sub in h.get("routes", []) or []:
                            for ih in sub.get("handle", []) or []:
                                ihname = ih.get("handler")
                                if ihname:
                                    names.add(ihname)
                return names

            # Split payloads by routing strategy. Path-matched routes
            # (the primary `/preview/<slug>/*` match) sit just before
            # the safety-net / IDE catch-all — they only need to
            # outrank those two. Header-only fallbacks (Referer +
            # cookie pinning) need to outrank EVERY other path-keyed
            # handler (`/_gapt/api/*`, `/health`, `/preview/* 404`,
            # IDE) so that ambiguous root-relative requests (e.g.
            # in-iframe nav to `/projects`) are steered back to the
            # preview upstream rather than landing on a leftover
            # GAPT route. With `/_gapt/*` namespacing this matters
            # less than it did — the only routes outside `/_gapt/*`
            # on the apex are `/health`, `/preview/*` and the default
            # 404 — but keeping the fallback ordering means upgrades
            # that add new apex paths (e.g. `/.well-known/...`) don't
            # accidentally hijack preview traffic.
            # plane.
            def _is_header_only(p: dict[str, Any]) -> bool:
                m = (p.get("match") or [{}])[0]
                has_header_match = (
                    "header_regexp" in m or "header" in m
                )
                return has_header_match and not m.get("path")

            referer_payloads = [p for p in payloads if _is_header_only(p)]
            path_payloads = [p for p in payloads if not _is_header_only(p)]

            # Anchor for the path-keyed routes: the safety-net 404 or
            # the IDE catch-all, whichever comes first.
            path_anchor = len(arr)
            for i, r in enumerate(arr):
                handlers = _all_handlers(r)
                paths = (r.get("match") or [{}])[0].get("path") or []
                is_preview_safety_net = any(
                    isinstance(p, str) and p.startswith("/preview") for p in paths
                ) and "static_response" in handlers
                is_ide_catchall = (not r.get("match")) and bool(
                    handlers & {"subroute", "reverse_proxy", "file_server"}
                )
                if is_preview_safety_net or is_ide_catchall:
                    path_anchor = i
                    break

            for offset, payload in enumerate(path_payloads):
                arr.insert(path_anchor + offset, payload)

            # Anchor for the Referer-keyed fallback: directly after
            # the leading `encode` header filter (which has no match
            # and a non-routing handler). When the host doesn't ship
            # an `encode` block at all, this drops to index 0 — also
            # fine, just means the fallback is the first considered.
            referer_anchor = 0
            for i, r in enumerate(arr):
                handlers = _all_handlers(r)
                if not r.get("match") and "encode" in handlers:
                    referer_anchor = i + 1
                    break
            for offset, payload in enumerate(referer_payloads):
                arr.insert(referer_anchor + offset, payload)
            # DELETE then POST — see method docstring.
            try:
                await self.client.delete(self.routes_path)
            except CaddyAdminError:
                # If the path was already empty, the GET above returned
                # None and the DELETE 404s; either way safe to proceed.
                pass
            await self.client.post(self.routes_path, arr)

    async def unregister(self, workspace_slug: str) -> None:
        """Delete the primary preview route plus its Referer fallback
        and (optional) cookie fallback. Idempotent — 404 means
        "already gone" and is swallowed."""
        primary = _route_id(workspace_slug)
        for route_id in (primary, f"{primary}-asset", f"{primary}-cookie"):
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
