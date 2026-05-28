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

    Upsert sequence: GET current routes → DELETE the array → POST
    the modified array back. The path-mode route is spliced *before*
    any no-match catch-all so it runs before the IDE fallback."""
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
    # Three-call sequence: GET, DELETE, POST.
    assert [c[0] for c in calls] == ["GET", "DELETE", "POST"]
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
async def test_subdomain_manager_unregister_targets_id_path() -> None:
    """Unregister deletes the primary route, its Referer fallback,
    AND its cookie-pinning fallback (when cookie pinning was
    registered). All three are idempotent — 404 on any one is
    silently swallowed (covered separately)."""
    seen_paths: list[str] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        seen_paths.append(path)
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.unregister("01KWS")
    assert seen_paths == [
        "/id/gapt-preview-01kws",
        "/id/gapt-preview-01kws-asset",
        "/id/gapt-preview-01kws-cookie",
        "/id/gapt-preview-01kws-cookie-doc",
    ]


@pytest.mark.asyncio
async def test_subdomain_manager_unregister_idempotent_on_404() -> None:
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
    assert "headers" not in proxy
