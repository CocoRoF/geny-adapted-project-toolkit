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
    assert new_route["handle"][0]["handler"] == "rewrite"
    assert new_route["handle"][0]["strip_path_prefix"] == "/preview/01kws"
    assert new_route["handle"][1]["upstreams"][0]["dial"] == "gapt-ws-01kws:3000"


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
    seen_paths: list[str] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        seen_paths.append(path)
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.unregister("01KWS")
    assert seen_paths == ["/id/gapt-preview-01kws"]


@pytest.mark.asyncio
async def test_subdomain_manager_unregister_idempotent_on_404() -> None:
    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        return (404, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="gapt.example")
    await manager.unregister("01KWS")  # must not raise
