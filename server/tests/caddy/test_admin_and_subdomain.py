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
    assert new_route["handle"][0]["handler"] == "reverse_proxy"
    assert new_route["handle"][0]["upstreams"][0]["dial"] == "gapt-ws-01kws:3000"


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
    assert primary["handle"][0]["handler"] == "rewrite"
    assert primary["handle"][0]["strip_path_prefix"] == "/preview/01kws"
    assert primary["handle"][1]["handler"] == "reverse_proxy"


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
    """Unregister deletes both the primary route AND its Referer
    fallback (the route that catches `/favicon.png`-style root-
    relative assets emitted by the upstream app)."""
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
    """Prod apps emit root-relative URLs — `/api/v1/posts`, `/_next/
    static/...`, etc. The Referer-keyed `-asset` fallback must
    outrank the GAPT control-plane `/api/*` route, otherwise those
    XHR calls 404 against the wrong upstream. The path-keyed
    primary route can stay near the bottom (it only needs to beat
    the safety net + IDE)."""
    calls: list[tuple[str, str, Any]] = []
    existing = [
        {"handle": [{"handler": "encode"}]},
        {"match": [{"path": ["/health"]}], "handle": [{"handler": "reverse_proxy"}]},
        {"match": [{"path": ["/api/*"]}], "handle": [{"handler": "reverse_proxy"}]},
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
        if "/api/*" in (r.get("match") or [{}])[0].get("path", [])
    )
    assert asset_idx < api_idx, (
        "Referer-fallback must run BEFORE /api/* so prod XHR to /api/v1/... "
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
