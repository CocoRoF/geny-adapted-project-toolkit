"""Caddy admin client + preview-route manager — verb mapping + payload shape."""

from __future__ import annotations

from typing import Any

import pytest

from gapt_server.domains.caddy import (
    CaddyAdminClient,
    CaddyAdminError,
    SubdomainBinding,
    SubdomainManager,
)
from gapt_server.domains.caddy.subdomain import PreviewMode


@pytest.mark.asyncio
async def test_admin_client_get_returns_body_on_200() -> None:
    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        assert method == "GET"
        assert path == "/config/test"
        return (200, {"hello": "world"})

    client = CaddyAdminClient(transport=transport)
    assert await client.get("/config/test") == {"hello": "world"}


@pytest.mark.asyncio
async def test_admin_client_get_returns_none_on_404() -> None:
    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        return (404, None)

    client = CaddyAdminClient(transport=transport)
    assert await client.get("/config/missing") is None


@pytest.mark.asyncio
async def test_admin_client_put_failure_raises() -> None:
    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        return (500, None)

    client = CaddyAdminClient(transport=transport)
    with pytest.raises(CaddyAdminError) as exc:
        await client.put("/config/x", {"a": 1})
    assert exc.value.code == "caddy.admin.put_failed"


@pytest.mark.asyncio
async def test_admin_client_delete_swallows_404() -> None:
    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        return (404, None)

    client = CaddyAdminClient(transport=transport)
    await client.delete("/id/nope")  # must not raise


@pytest.mark.asyncio
async def test_subdomain_manager_path_mode_default() -> None:
    """Path-mode is the default — single apex domain, route matches
    `/preview/<slug>` and strips the prefix before reverse-proxy.

    Upsert sequence (Phase N.3): drift-check GET → upsert GET → PATCH
    with the modified array. The DELETE-then-POST cycle was retired
    because the brief empty-array window between the two calls reset
    every keep-alive connection through Caddy. The path-mode route
    is spliced *before* any no-match catch-all so it runs before the
    IDE fallback."""
    calls: list[tuple[str, str, Any]] = []
    existing = [
        {"handle": [{"handler": "encode"}]},  # leading header filter
        {  # the IDE catch-all
            "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "ide:35173"}]}],
        },
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    host = await manager.register(
        SubdomainBinding(workspace_slug="01KWS", upstream_host="gapt-ws-01kws", upstream_port=3000)
    )

    assert host == "gapt.example/preview/01kws"
    # Phase N.3 — three-call sequence: drift-check GET, _upsert GET,
    # atomic PATCH with the new array. Pre-N.3 this was four calls
    # (GET / GET / DELETE / POST) and the DELETE→POST window left the
    # array empty for a few hundred ms, which reset every keep-alive
    # connection (vite HMR, chat SSE, polling) flowing through Caddy
    # — the visible symptom was the IDE force-reloading itself every
    # time the operator clicked Expose / Stop / Deploy. PATCH applies
    # in place: routes byte-identical between old and new keep their
    # handler chains; only changed routes get rebuilt.
    assert [c[0] for c in calls] == ["GET", "GET", "PATCH"]
    assert calls[2][1] == "/config/apps/http/servers/main/routes"
    posted = calls[2][2]
    # The IDE catch-all should still be present, and our new route
    # should be inserted BEFORE it.
    ide_idx = next(
        i for i, r in enumerate(posted) if not r.get("match") and any(
            h.get("handler") == "reverse_proxy" for h in r.get("handle", [])
        )
    )
    new_idx = next(i for i, r in enumerate(posted) if r.get("@id") == "gapt-preview-01kws")
    assert new_idx < ide_idx
    new_route = posted[new_idx]
    assert new_route["match"][0]["path"] == ["/preview/01kws", "/preview/01kws/*"]
    # Default: no prefix strip — the app keeps `/preview/<slug>` in
    # its received URL so basePath-aware apps emit matching URLs.
    # With cookie pinning on by default, the primary handler list
    # is [headers (Set-Cookie), reverse_proxy]; the proxy isn't at
    # index 0 anymore — find it by handler name.
    proxy = next(h for h in new_route["handle"] if h["handler"] == "reverse_proxy")
    assert proxy["upstreams"][0]["dial"] == "gapt-ws-01kws:3000"


@pytest.mark.asyncio
async def test_subdomain_manager_path_mode_strip_prefix_opt_in() -> None:
    """Apps that need the prefix removed (no basePath) opt in via
    `strip_prefix=True`; Caddy then strips before reverse_proxy."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
            strip_prefix=True,
        )
    )
    posted = calls[2][2]
    # The path-match primary route carries the rewrite handler (the
    # Referer-only `-asset` fallback no longer rewrites since
    # strip_prefix=True means the upstream serves at root). Find it
    # by @id rather than positional indexing — the splice puts the
    # Referer-fallback ahead of the primary now.
    primary = next(r for r in posted if r.get("@id") == "gapt-preview-01kws")
    # With cookie pinning on, the handler chain is
    # [headers (Set-Cookie), rewrite (strip), reverse_proxy].
    rewrite = next(h for h in primary["handle"] if h["handler"] == "rewrite")
    assert rewrite["strip_path_prefix"] == "/preview/01kws"
    assert any(h["handler"] == "reverse_proxy" for h in primary["handle"])


@pytest.mark.asyncio
async def test_subdomain_manager_subdomain_mode() -> None:
    """Subdomain mode preserved for apps that need root-relative URLs."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="preview.gapt.example")
    host = await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="10.0.0.5",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )

    assert host == "01kws.preview.gapt.example"
    posted = calls[2][2]
    assert posted[0]["match"][0]["host"] == ["01kws.preview.gapt.example"]
    assert posted[0]["handle"][0]["handler"] == "reverse_proxy"
    assert posted[0]["handle"][0]["upstreams"][0]["dial"] == "10.0.0.5:3000"


@pytest.mark.asyncio
async def test_subdomain_manager_unregister_batches_one_patch() -> None:
    """Unregister removes the primary route AND every fallback sibling
    (Referer / cookie / cookie-doc) in a single PATCH of the routes
    array — one Caddy config reload instead of the four sequential
    `DELETE /id/*` reloads the old implementation issued (each reload
    churns every proxied connection)."""
    calls: list[tuple[str, str, Any]] = []
    existing = [
        {"@id": "gapt-preview-01kws", "match": [{"path": ["/preview/01kws*"]}]},
        {"@id": "gapt-preview-01kws-asset", "match": [{"header": {}}]},
        {"@id": "gapt-preview-01kws-cookie", "match": [{"header": {}}]},
        {"@id": "unrelated-route", "match": [{"path": ["/x"]}]},
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, existing)
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.unregister("01KWS")
    patches = [c for c in calls if c[0] == "PATCH"]
    assert len(patches) == 1
    kept = patches[0][2]
    assert [r["@id"] for r in kept] == ["unrelated-route"]


@pytest.mark.asyncio
async def test_subdomain_manager_unregister_noop_when_absent() -> None:
    """When none of the slug's @ids exist, unregister must not PATCH
    at all — zero admin mutations means zero Caddy reloads."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [{"@id": "unrelated-route"}])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.unregister("01KWS")  # must not raise
    assert [c[0] for c in calls] == ["GET"]


@pytest.mark.asyncio
async def test_subdomain_manager_unregister_idempotent_on_error() -> None:
    """A failing GET (admin hiccup) degrades to an empty array →
    nothing to remove → no PATCH, no raise."""

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        return (404, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.unregister("01KWS")  # must not raise


@pytest.mark.asyncio
async def test_upsert_inserts_before_preview_safety_net_404() -> None:
    """The Caddyfile now ships a `/preview/*` static_response 404 ahead
    of the IDE catch-all so unregistered slugs don't fall through to
    the SPA (iframe-in-iframe recursion bug). Dynamic per-slug routes
    must splice in BEFORE that 404 — otherwise the safety-net wins
    and every Expose appears broken."""
    calls: list[tuple[str, str, Any]] = []
    existing = [
        {"handle": [{"handler": "encode"}]},
        {"match": [{"path": ["/health"]}], "handle": [{"handler": "reverse_proxy"}]},
        {
            "match": [{"path": ["/preview", "/preview/*"]}],
            "handle": [{"handler": "static_response", "status_code": 404}],
        },
        {"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "ide:35173"}]}]},
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(workspace_slug="01KWS", upstream_host="gapt-ws-01kws", upstream_port=3000)
    )
    posted = calls[2][2]
    new_idx = next(i for i, r in enumerate(posted) if r.get("@id") == "gapt-preview-01kws")
    safety_net_idx = next(
        i
        for i, r in enumerate(posted)
        if any(
            isinstance(p, str) and p.startswith("/preview")
            for p in (r.get("match") or [{}])[0].get("path", [])
        )
        and any(h.get("handler") == "static_response" for h in r.get("handle", []))
    )
    assert new_idx < safety_net_idx, (
        "dynamic /preview/<slug> route must run BEFORE the safety-net 404"
    )


@pytest.mark.asyncio
async def test_upsert_referer_fallback_lands_above_api_route() -> None:
    """Prod apps emit root-relative URLs — `/_gapt/api/v1/posts`, `/_next/
    static/...`, etc. The Referer-keyed `-asset` fallback must
    outrank the GAPT control-plane `/_gapt/api/*` route, otherwise those
    XHR calls 404 against the wrong upstream. The path-keyed
    primary route can stay near the bottom (it only needs to beat
    the safety net + IDE)."""
    calls: list[tuple[str, str, Any]] = []
    existing = [
        {"handle": [{"handler": "encode"}]},
        {"match": [{"path": ["/health"]}], "handle": [{"handler": "reverse_proxy"}]},
        {"match": [{"path": ["/_gapt/api/*"]}], "handle": [{"handler": "reverse_proxy"}]},
        {
            "match": [{"path": ["/preview", "/preview/*"]}],
            "handle": [{"handler": "static_response", "status_code": 404}],
        },
        {"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "ide:35173"}]}]},
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
            strip_prefix=True,
        )
    )
    posted = calls[2][2]
    asset_idx = next(
        i for i, r in enumerate(posted) if r.get("@id") == "gapt-preview-01kws-asset"
    )
    api_idx = next(
        i
        for i, r in enumerate(posted)
        if "/_gapt/api/*" in (r.get("match") or [{}])[0].get("path", [])
    )
    assert asset_idx < api_idx, (
        "Referer-fallback must run BEFORE /_gapt/api/* so prod XHR to /_gapt/api/v1/... "
        "hits the deployed app, not the GAPT control plane"
    )
    # Referer-fallback should also outrank the safety-net 404 + IDE.
    primary_idx = next(
        i for i, r in enumerate(posted) if r.get("@id") == "gapt-preview-01kws"
    )
    safety_idx = next(
        i
        for i, r in enumerate(posted)
        if any(
            isinstance(p, str) and p.startswith("/preview")
            for p in (r.get("match") or [{}])[0].get("path", [])
        )
        and any(h.get("handler") == "static_response" for h in r.get("handle", []))
    )
    assert asset_idx < primary_idx < safety_idx


@pytest.mark.asyncio
async def test_subdomain_manager_https_upstream_with_host_override() -> None:
    """Operator's prod stack may front services with its own nginx
    that forces HTTPS, presents a cert for the public domain (not
    the docker DNS name), and routes on `server_name`. The binding's
    transport knobs (`upstream_scheme=https`, `upstream_host_header`,
    `upstream_tls_insecure`) should produce a Caddy reverse_proxy
    handler that talks HTTPS, skips TLS verification, and rewrites
    the outgoing Host header."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="nginx",
            upstream_port=443,
            upstream_scheme="https",
            upstream_host_header="hrletsgo.me",
            upstream_tls_insecure=True,
        )
    )
    posted = calls[2][2]
    primary = next(r for r in posted if r.get("@id") == "gapt-preview-01kws")
    proxy = next(h for h in primary["handle"] if h.get("handler") == "reverse_proxy")
    assert proxy["transport"]["protocol"] == "http"
    assert proxy["transport"]["tls"]["insecure_skip_verify"] is True
    assert proxy["headers"]["request"]["set"]["Host"] == ["hrletsgo.me"]
    assert proxy["upstreams"][0]["dial"] == "nginx:443"


@pytest.mark.asyncio
async def test_cookie_pinning_sets_short_lived_cookie_and_fallback_route() -> None:
    """Cookie pinning (path mode, on by default) injects a
    `Set-Cookie` header on the primary `/preview/<slug>` route and
    registers a third Caddy route that catches root-relative
    follow-up requests bearing the cookie + Sec-Fetch-Site=same-
    origin. This is the path-mode-only workaround for apex URL
    collisions — subdomain mode doesn't need it."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
        )
    )
    posted = calls[2][2]
    # Primary route carries Set-Cookie via a `headers` response handler.
    primary = next(r for r in posted if r.get("@id") == "gapt-preview-01kws")
    headers_handler = next(h for h in primary["handle"] if h["handler"] == "headers")
    cookie_header = headers_handler["response"]["set"]["Set-Cookie"][0]
    assert "gapt_preview=01kws" in cookie_header
    assert "Path=/" in cookie_header
    # Default TTL was bumped 5min → 24h (`86400s`) to make path mode
    # survive refreshes / new tabs without losing the cookie. Cookie
    # remains HttpOnly + SameSite=Lax + Path=/ so pollution risk
    # stays bounded.
    assert "Max-Age=86400" in cookie_header
    assert "SameSite=Lax" in cookie_header
    assert "HttpOnly" in cookie_header

    # Cookie-fallback route exists, matches cookie + same-origin
    # Sec-Fetch-Site, reverse-proxies to the same upstream.
    cookie_route = next(
        r for r in posted if r.get("@id") == "gapt-preview-01kws-cookie"
    )
    match = cookie_route["match"][0]
    assert "gapt_preview=01kws" in match["header_regexp"]["Cookie"]["pattern"]
    assert match["header"]["Sec-Fetch-Site"] == ["same-origin"]
    proxy = next(h for h in cookie_route["handle"] if h["handler"] == "reverse_proxy")
    assert proxy["upstreams"][0]["dial"] == "gapt-ws-01kws:3000"


@pytest.mark.asyncio
async def test_cookie_pinning_disabled_when_ttl_zero() -> None:
    """`cookie_pinning_ttl_s=0` opts out: primary route has no
    Set-Cookie header, and no `-cookie` fallback route is registered.
    Subdomain-mode bindings should always use this (or just rely on
    the host header — cookie pinning is path-mode-only)."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
            cookie_pinning_ttl_s=0,
        )
    )
    posted = calls[2][2]
    primary = next(r for r in posted if r.get("@id") == "gapt-preview-01kws")
    assert not any(h["handler"] == "headers" for h in primary["handle"])
    assert not any(
        isinstance(r, dict) and r.get("@id") == "gapt-preview-01kws-cookie"
        for r in posted
    )


@pytest.mark.asyncio
async def test_switching_from_path_to_subdomain_drops_stale_fallback_routes() -> None:
    """When an env is re-routed from path to subdomain mode, the old
    `-asset` and `-cookie` fallback routes must be cleared from the
    routes array. Otherwise they'd keep catching root-relative
    requests forever even though the user-facing URL is now a
    subdomain. The full @id family (`gapt-preview-<slug>`, `-asset`,
    `-cookie`) is dropped before the fresh subdomain payload is
    spliced in."""
    calls: list[tuple[str, str, Any]] = []
    # Existing state — a path-mode binding's three routes plus an
    # IDE catch-all.
    existing = [
        {"handle": [{"handler": "encode"}]},
        {
            "@id": "gapt-preview-01kws-asset",
            "match": [{"header_regexp": {"Referer": {"pattern": "...01kws.../"}}}],
            "handle": [{"handler": "reverse_proxy"}],
        },
        {
            "@id": "gapt-preview-01kws-cookie",
            "match": [{"header_regexp": {"Cookie": {"pattern": "gapt_preview=01kws"}}}],
            "handle": [{"handler": "reverse_proxy"}],
        },
        {
            "@id": "gapt-preview-01kws",
            "match": [{"path": ["/preview/01kws", "/preview/01kws/*"]}],
            "handle": [{"handler": "reverse_proxy"}],
        },
        {"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "ide"}]}]},
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )
    posted = calls[2][2]
    # Exactly one route bearing the slug family should remain — the
    # new host-keyed subdomain route. The old `-asset` and `-cookie`
    # routes must have been cleared.
    slug_family_ids = {"gapt-preview-01kws", "gapt-preview-01kws-asset", "gapt-preview-01kws-cookie"}
    remaining = [r for r in posted if r.get("@id") in slug_family_ids]
    assert len(remaining) == 1
    assert remaining[0]["@id"] == "gapt-preview-01kws"
    assert remaining[0]["match"][0]["host"] == ["01kws.gapt.example"]


@pytest.mark.asyncio
async def test_cookie_fallback_lands_above_api_route_and_safety_net() -> None:
    """The cookie fallback has to outrank `/_gapt/api/*` and the safety-
    net 404 just like the Referer fallback does — both are header-
    only matchers for catching root-relative requests that bypass
    `/preview/<slug>`."""
    calls: list[tuple[str, str, Any]] = []
    existing = [
        {"handle": [{"handler": "encode"}]},
        {"match": [{"path": ["/_gapt/api/*"]}], "handle": [{"handler": "reverse_proxy"}]},
        {
            "match": [{"path": ["/preview", "/preview/*"]}],
            "handle": [{"handler": "static_response", "status_code": 404}],
        },
        {"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "ide:35173"}]}]},
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(workspace_slug="01KWS", upstream_host="gapt-ws-01kws", upstream_port=3000)
    )
    posted = calls[2][2]
    cookie_idx = next(
        i for i, r in enumerate(posted) if r.get("@id") == "gapt-preview-01kws-cookie"
    )
    api_idx = next(
        i for i, r in enumerate(posted)
        if "/_gapt/api/*" in (r.get("match") or [{}])[0].get("path", [])
    )
    assert cookie_idx < api_idx


@pytest.mark.asyncio
async def test_cookie_doc_redirect_route_exists_and_uses_307() -> None:
    """The cookie-doc fallback is the URL-bar fix for path mode: when
    the browser navigates (Sec-Fetch-Dest=document) with the preview
    cookie set, instead of transparently proxying we 307-redirect
    back to `/preview/<slug>{path}` so the address bar matches the
    served content.

    Regression guard: status MUST be 307 (preserves method for form
    POSTs), Location MUST include `/preview/<slug>{http.request.uri}`,
    and the match must include `Sec-Fetch-Dest: document` AND the
    reserved-path `not` clause (otherwise GAPT IDE navigation gets
    redirected too)."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
        )
    )
    posted = calls[2][2]
    doc = next(
        (r for r in posted if r.get("@id") == "gapt-preview-01kws-cookie-doc"),
        None,
    )
    assert doc is not None, "cookie-doc redirect route missing"
    match = doc["match"][0]
    assert match["header"]["Sec-Fetch-Site"] == ["same-origin"]
    assert match["header"]["Sec-Fetch-Dest"] == ["document"]
    assert "not" in match  # reserved-path guard
    handler = doc["handle"][0]
    assert handler["handler"] == "static_response"
    assert handler["status_code"] == 307
    location = handler["headers"]["Location"][0]
    assert location == "/preview/01kws{http.request.uri}"


@pytest.mark.asyncio
async def test_cookie_doc_redirect_excluded_when_pinning_off() -> None:
    """When `cookie_pinning_ttl_s=0`, neither the cookie-doc redirect
    nor the cookie-asset fallback should be registered — they both
    rely on the cookie that won't be set in that mode."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
            cookie_pinning_ttl_s=0,
        )
    )
    posted = calls[2][2]
    cookie_doc = [r for r in posted if r.get("@id") == "gapt-preview-01kws-cookie-doc"]
    cookie = [r for r in posted if r.get("@id") == "gapt-preview-01kws-cookie"]
    assert cookie_doc == [], "cookie-doc should not exist when pinning disabled"
    assert cookie == [], "cookie-asset should not exist when pinning disabled"


@pytest.mark.asyncio
async def test_fallback_routes_exclude_gapt_reserved_paths() -> None:
    """Both the Referer-fallback and Cookie-fallback routes must carry
    a `not: [{path: [/_gapt/*, /preview, /preview/*, /health]}]` clause.

    Without this guard, ONE preview visit poisoned the user's browser
    with `gapt_preview=<slug>` cookie + the preview's Referer, and
    every subsequent same-origin click on a GAPT path (the dashboard,
    /_gapt/api/* XHRs, /health probes) got hijacked to the preview
    upstream — surfacing as 502s when the preview stack had stopped.

    The `not` clause keeps the fallback narrow: only requests outside
    GAPT's reserved namespace fall through to the preview upstream."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
        )
    )
    posted = calls[2][2]
    expected_reserved = ["/_gapt/*", "/preview", "/preview/*", "/health"]
    for rid_suffix in ("-asset", "-cookie"):
        route = next(
            (r for r in posted if r.get("@id") == f"gapt-preview-01kws{rid_suffix}"),
            None,
        )
        assert route is not None, f"{rid_suffix} route missing"
        match = route["match"][0]
        assert "not" in match, f"{rid_suffix} route missing `not` clause"
        not_paths = match["not"][0].get("path") or []
        assert not_paths == expected_reserved, (
            f"{rid_suffix} `not.path` mismatch: got {not_paths}"
        )


@pytest.mark.asyncio
async def test_zone_catchall_404_inserted_after_specific_hosts() -> None:
    """When a subdomain route is registered, a zone-wide catch-all
    404 lands right after the specific host routes. Unregistered
    `<random-slug>.<preview-domain>` requests hit the 404 (terminal)
    instead of falling through to apex path matchers → no more
    "stack stopped → URL redirects to GAPT IDE" bug."""
    calls: list[tuple[str, str, Any]] = []
    existing = [
        {"handle": [{"handler": "encode"}]},
        {"match": [{"path": ["/_gapt/app/*"]}], "handle": [{"handler": "reverse_proxy"}]},
        {"match": [{"path": ["/"]}], "handle": [{"handler": "static_response"}]},
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(
        client=client,
        preview_domain="hrletsgo.me",
        gapt_apex_host="gapt.hrletsgo.me",
    )
    await manager.register(
        SubdomainBinding(
            workspace_slug="test",
            upstream_host="upstream",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )
    posted = calls[2][2]
    # [0] specific host route → terminal
    # [1] zone catch-all 404 → terminal, with `not` clause for the
    #     GAPT apex so visiting GAPT itself still works
    assert posted[0].get("@id") == "gapt-preview-test"
    assert posted[1].get("@id") == "gapt-preview-zone-catchall"
    assert posted[1].get("terminal") is True
    catchall_match = posted[1].get("match", [{}])[0]
    assert catchall_match.get("host") == ["*.hrletsgo.me"]
    assert catchall_match.get("not") == [{"host": ["gapt.hrletsgo.me"]}]
    handler = posted[1].get("handle", [{}])[0]
    assert handler.get("handler") == "static_response"
    assert handler.get("status_code") == 404


@pytest.mark.asyncio
async def test_zone_catchall_no_exclusion_when_apex_unset() -> None:
    """When `gapt_apex_host` is None (preview_domain is a strict
    sub-host of the GAPT apex — e.g. `previews.gapt.example`), the
    catch-all skips the `not` clause."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    manager = SubdomainManager(
        client=CaddyAdminClient(transport=transport),
        preview_domain="previews.gapt.example",
        gapt_apex_host=None,
    )
    await manager.register(
        SubdomainBinding(
            workspace_slug="abc",
            upstream_host="upstream",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )
    posted = calls[2][2]
    catchall = next(p for p in posted if p.get("@id") == "gapt-preview-zone-catchall")
    matcher = catchall.get("match", [{}])[0]
    assert matcher.get("host") == ["*.previews.gapt.example"]
    assert "not" not in matcher


@pytest.mark.asyncio
async def test_subdomain_mode_host_route_inserted_at_index_0() -> None:
    """Subdomain-mode routes are HOST-matched and `terminal: true`.
    Without splicing them at index 0, the apex Caddyfile's path-only
    handlers (`/_gapt/api/*`, `/_gapt/app/*`, `/`, etc.) win for any
    request to `<slug>.<preview-domain>` because path matchers don't
    care about Host. That's the bug that made `test.hrletsgo.me/`
    serve the GAPT IDE instead of the user's prod stack."""
    calls: list[tuple[str, str, Any]] = []
    existing = [
        {"handle": [{"handler": "encode"}]},
        {"match": [{"path": ["/_gapt/api/*"]}], "handle": [{"handler": "reverse_proxy"}]},
        {"match": [{"path": ["/_gapt/app/*"]}], "handle": [{"handler": "reverse_proxy"}]},
        {"match": [{"path": ["/health"]}], "handle": [{"handler": "reverse_proxy"}]},
        {"match": [{"path": ["/"]}], "handle": [{"handler": "static_response"}]},
        {
            "match": [{"path": ["/preview", "/preview/*"]}],
            "handle": [{"handler": "static_response", "status_code": 404}],
        },
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="hrletsgo.me")
    await manager.register(
        SubdomainBinding(
            workspace_slug="test",
            upstream_host="gapt-prod-01x-frontend",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )
    posted = calls[2][2]
    # The subdomain host route must be at index 0 — before every
    # path matcher.
    host_route = posted[0]
    assert host_route.get("@id") == "gapt-preview-test"
    assert host_route.get("terminal") is True
    match = host_route.get("match", [{}])[0]
    assert match.get("host") == ["test.hrletsgo.me"]
    assert "path" not in match


@pytest.mark.asyncio
async def test_subdomain_register_clears_old_mode_routes() -> None:
    """When a binding flips from path-mode to subdomain-mode (or
    slug changes), the previous mode's family (`-asset`, `-cookie`,
    `-cookie-doc`) must be cleared so they don't keep serving the
    old URL pattern forever."""
    calls: list[tuple[str, str, Any]] = []
    # Pre-existing path-mode family — should all be dropped.
    existing = [
        {"@id": "gapt-preview-01kws", "match": [{"path": ["/preview/01kws/*"]}]},
        {"@id": "gapt-preview-01kws-asset", "match": [{"header_regexp": {}}]},
        {"@id": "gapt-preview-01kws-cookie", "match": [{"header": {}}]},
        {"@id": "gapt-preview-01kws-cookie-doc", "match": [{"header_regexp": {}}]},
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01kws",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )
    posted = calls[2][2]
    ids = [r.get("@id") for r in posted]
    # All path-mode family entries dropped, only the new subdomain
    # host route should reference the slug.
    assert ids.count("gapt-preview-01kws") == 1
    assert "gapt-preview-01kws-asset" not in ids
    assert "gapt-preview-01kws-cookie" not in ids
    assert "gapt-preview-01kws-cookie-doc" not in ids


@pytest.mark.asyncio
async def test_subdomain_zone_split_from_preview_domain() -> None:
    """When `subdomain_zone` is set, subdomain-mode bindings AND the
    zone catch-all use it (not `preview_domain`). Real-world driver:
    GAPT lives at `gapt.hrletsgo.me` (a 2-level subdomain) so path
    mode wants `/preview/<slug>` under `gapt.hrletsgo.me`, but
    Cloudflare's free wildcard cert only covers `*.hrletsgo.me` —
    subdomain mode therefore MUST resolve to `<slug>.hrletsgo.me`,
    not `<slug>.gapt.hrletsgo.me` (which has no cert)."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(
        client=client,
        preview_domain="gapt.hrletsgo.me",
        subdomain_zone="hrletsgo.me",
        gapt_apex_host="gapt.hrletsgo.me",
    )
    host = await manager.register(
        SubdomainBinding(
            workspace_slug="hr-test",
            upstream_host="gapt-prod-01x-frontend",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )
    # Returned host uses the zone, not preview_domain.
    assert host == "hr-test.hrletsgo.me"
    posted = calls[2][2]
    host_route = next(r for r in posted if r.get("@id") == "gapt-preview-hr-test")
    assert host_route["match"][0]["host"] == ["hr-test.hrletsgo.me"]
    # Zone catch-all matches `*.<subdomain_zone>` and excludes the
    # GAPT IDE host so visiting `gapt.hrletsgo.me` itself still works.
    catchall = next(
        r for r in posted if r.get("@id") == "gapt-preview-zone-catchall"
    )
    matcher = catchall["match"][0]
    assert matcher["host"] == ["*.hrletsgo.me"]
    assert matcher.get("not") == [{"host": ["gapt.hrletsgo.me"]}]


@pytest.mark.asyncio
async def test_subdomain_zone_unset_falls_back_to_preview_domain() -> None:
    """Back-compat: when `subdomain_zone` is None, subdomain mode
    keeps using `preview_domain` (the legacy single-zone install)."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="hrletsgo.me")
    host = await manager.register(
        SubdomainBinding(
            workspace_slug="hr-test",
            upstream_host="gapt-prod-01x-frontend",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )
    assert host == "hr-test.hrletsgo.me"


@pytest.mark.asyncio
async def test_subdomain_manager_http_upstream_omits_transport() -> None:
    """Default HTTP upstream — no `transport`, no `headers` keys on
    the reverse_proxy handler. Keeps the route payload compact and
    backwards-compatible with the existing test fleet."""
    calls: list[tuple[str, str, Any]] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, [])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="gapt-ws-01kws",
            upstream_port=3000,
        )
    )
    posted = calls[2][2]
    primary = next(r for r in posted if r.get("@id") == "gapt-preview-01kws")
    proxy = next(h for h in primary["handle"] if h.get("handler") == "reverse_proxy")
    assert "transport" not in proxy


@pytest.mark.asyncio
async def test_register_short_circuits_when_subdomain_route_already_current() -> None:
    """Phase N.3 — drift-free reconciler tick.

    When the live Caddy route under the binding's @id already has the
    expected host + upstream dial, ``register`` returns the URL host
    WITHOUT executing the DELETE-then-POST cycle inside ``_upsert``.

    Driver: the 5-minute reconciler loop was calling register() every
    tick on every active env. Each register triggered DELETE-then-
    POST on the full routes array which left Caddy with zero routes
    for a few hundred ms — long enough to reset every keep-alive
    connection (vite HMR websocket, chat SSE streams, ...). With the
    IDE itself proxied through Caddy, the HMR reset was being
    interpreted by the browser as "force reload page" and the IDE
    refreshed every five minutes for the operator. This short-circuit
    is the fix."""
    calls: list[tuple[str, str, Any]] = []
    expected_dial = "10.0.0.5:3000"
    current_route = {
        "@id": "gapt-preview-01kws",
        "match": [{"host": ["01kws.preview.gapt.example"]}],
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": expected_dial}],
                # Drift check compares the FULL generated handler —
                # the live route must carry today's shape (incl. the
                # reload-survival grace) to count as current.
                "stream_close_delay": "1h",
            }
        ],
        "terminal": True,
    }
    catchall = {
        "@id": "gapt-preview-zone-catchall",
        "match": [{"host": ["*.preview.gapt.example"]}],
        "handle": [{"handler": "static_response", "status_code": 404}],
        "terminal": True,
    }
    # Ordering invariant: host route BEFORE catchall, otherwise the
    # drift check will (correctly) flag the broken state and fall
    # through to a full upsert.
    routes_state = [current_route, catchall]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        # The drift check hits `/id/gapt-preview-01kws` — return the
        # already-registered route. It ALSO hits the routes path to
        # verify the catchall-ordering invariant.
        if method == "GET" and path == "/id/gapt-preview-01kws":
            return (200, current_route)
        if method == "GET" and path.endswith("/routes"):
            return (200, list(routes_state))
        # If _upsert is reached at all the test fails — assert below.
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="preview.gapt.example")
    host = await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="10.0.0.5",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )

    assert host == "01kws.preview.gapt.example"
    # Two GETs: /id/<route> for content drift, then /...routes for
    # ordering drift. No PATCH because both probes succeed.
    methods = [c[0] for c in calls]
    assert methods == ["GET", "GET"], methods
    assert calls[0][1] == "/id/gapt-preview-01kws"
    assert calls[1][1].endswith("/routes")


@pytest.mark.asyncio
async def test_register_does_full_upsert_when_catchall_ordering_drifted() -> None:
    """Phase N.3 — when the live route's content matches but the
    catchall sits AHEAD of it (the historical _upsert bug's leftover
    state), the drift check must detect the bad ordering and fall
    through to a full upsert so the PATCH re-positions things. This
    is what makes the system self-healing on the next register
    instead of silently leaving the user with a 404."""
    calls: list[tuple[str, str, Any]] = []
    current_route = {
        "@id": "gapt-preview-01kws",
        "match": [{"host": ["01kws.preview.gapt.example"]}],
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": "10.0.0.5:3000"}],
            }
        ],
        "terminal": True,
    }
    catchall = {
        "@id": "gapt-preview-zone-catchall",
        "match": [{"host": ["*.preview.gapt.example"]}],
        "handle": [{"handler": "static_response", "status_code": 404}],
        "terminal": True,
    }
    # BAD ordering — catchall ahead of host route.
    routes_state = [catchall, current_route]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET" and path == "/id/gapt-preview-01kws":
            return (200, current_route)
        if method == "GET" and path.endswith("/routes"):
            return (200, list(routes_state))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="preview.gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="10.0.0.5",
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )

    # Bad ordering detected → full upsert runs to fix.
    methods = [c[0] for c in calls]
    assert "PATCH" in methods, methods
    # The PATCH body must put host route BEFORE catchall.
    patch_body = next(c[2] for c in calls if c[0] == "PATCH")
    idx_host = next(
        i for i, r in enumerate(patch_body) if r.get("@id") == "gapt-preview-01kws"
    )
    idx_catchall = next(
        i for i, r in enumerate(patch_body)
        if r.get("@id") == "gapt-preview-zone-catchall"
    )
    assert idx_host < idx_catchall


@pytest.mark.asyncio
async def test_register_does_full_upsert_when_subdomain_dial_drifted() -> None:
    """Counterpart of the short-circuit test: when the live Caddy
    upstream dial has drifted (e.g. compose recreated the container
    so its name changed), register() must NOT short-circuit. The
    full DELETE-then-POST cycle re-registers the route with the new
    container name."""
    calls: list[tuple[str, str, Any]] = []
    stale_route = {
        "@id": "gapt-preview-01kws",
        "match": [{"host": ["01kws.preview.gapt.example"]}],
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": "old-container:3000"}],  # drift!
            }
        ],
        "terminal": True,
    }

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET" and path == "/id/gapt-preview-01kws":
            return (200, stale_route)
        if method == "GET":
            return (200, [stale_route])
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="preview.gapt.example")
    await manager.register(
        SubdomainBinding(
            workspace_slug="01KWS",
            upstream_host="new-container",  # drifted to a different name
            upstream_port=3000,
            mode=PreviewMode.SUBDOMAIN,
        )
    )

    # Drift detected → full upsert sequence (drift GET, upsert GET,
    # PATCH). The PATCHed array's matching route must point at the
    # NEW container.
    methods = [c[0] for c in calls]
    assert methods == ["GET", "GET", "PATCH"], methods
    posted = calls[2][2]
    new_route = next(r for r in posted if r.get("@id") == "gapt-preview-01kws")
    proxy = next(h for h in new_route["handle"] if h.get("handler") == "reverse_proxy")
    assert proxy["upstreams"][0]["dial"] == "new-container:3000"
    assert "headers" not in proxy


@pytest.mark.asyncio
async def test_catchall_lands_after_pre_existing_host_routes() -> None:
    """Phase N.3 — regression for "Preview not registered" on a live
    prod subdomain.

    Repro: after registering ``hr-test.hrletsgo.me`` (host mode) and
    ``blog.hrletsgo.me`` (host mode), the operator exposes a
    workspace dev service in PATH mode. The path-mode register has
    ``host_payloads == []``. Pre-fix the catch-all was spliced at
    index ``len(host_payloads) == 0`` — i.e. the very front of the
    array — placing it AHEAD of the previously-registered specific
    host routes. Caddy evaluates routes top-down and the catch-all
    is ``terminal: true`` matching ``*.<zone>``, so every subsequent
    request to ``hr-test.hrletsgo.me`` short-circuited to the
    catch-all's 404 ("Preview not registered.") and the deployed
    site became unreachable.

    The fix walks the head of the array past any host-only routes
    (new inserts + pre-existing bindings) and inserts the catch-all
    after them, preserving the "specific routes win first" ordering
    Caddy requires."""
    calls: list[tuple[str, str, Any]] = []
    # Pre-existing array: two prod subdomain host routes followed by
    # the apex encode block. The catch-all was registered earlier
    # but the test simulates a path-mode register on TOP of this
    # state — without the fix, the splice would push the catch-all
    # ahead of hr-test / blog.
    existing = [
        {
            "@id": "gapt-preview-hr-test",
            "match": [{"host": ["hr-test.hrletsgo.me"]}],
            "handle": [
                {
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "hr-prod:3000"}],
                }
            ],
            "terminal": True,
        },
        {
            "@id": "gapt-preview-blog",
            "match": [{"host": ["blog.hrletsgo.me"]}],
            "handle": [
                {
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "blog-prod:3000"}],
                }
            ],
            "terminal": True,
        },
        {  # leading encode (no match — not host-only)
            "handle": [{"handler": "encode"}],
        },
        {  # IDE catch-all
            "handle": [
                {
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": "ide:35173"}],
                }
            ],
        },
    ]

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return (200, list(existing))
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="hrletsgo.me")
    await manager.register(
        SubdomainBinding(
            workspace_slug="newslug",
            upstream_host="gapt-ws-xyz",
            upstream_port=3000,
            # Path mode — host_payloads == [], the exact scenario that
            # would push catch-all to index 0 under the old logic.
            mode=PreviewMode.PATH,
        )
    )

    posted = calls[2][2]
    # Index of every route relevant to the ordering invariant.
    idx_hr = next(
        i for i, r in enumerate(posted)
        if r.get("@id") == "gapt-preview-hr-test"
    )
    idx_blog = next(
        i for i, r in enumerate(posted)
        if r.get("@id") == "gapt-preview-blog"
    )
    idx_catchall = next(
        i for i, r in enumerate(posted)
        if r.get("@id") == "gapt-preview-zone-catchall"
    )
    # The invariant: catch-all comes AFTER every specific host route
    # already registered. Without this the wildcard host match wins
    # in Caddy's top-down evaluation and the specific subdomain
    # returns 404 "Preview not registered."
    assert idx_hr < idx_catchall, (
        f"hr-test must precede catch-all (idx_hr={idx_hr}, "
        f"idx_catchall={idx_catchall}) — Caddy evaluates top-down "
        f"and the wildcard would win"
    )
    assert idx_blog < idx_catchall, (
        f"blog must precede catch-all (idx_blog={idx_blog}, "
        f"idx_catchall={idx_catchall})"
    )
