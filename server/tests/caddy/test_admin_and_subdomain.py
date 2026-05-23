"""Caddy admin client + subdomain manager — verb mapping + payload shape."""

from __future__ import annotations

from typing import Any

import pytest

from gapt_server.domains.caddy import (
    CaddyAdminClient,
    CaddyAdminError,
    SubdomainBinding,
    SubdomainManager,
)


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
async def test_subdomain_manager_register_posts_canonical_route() -> None:
    captured: dict[str, Any] = {}

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="preview.gapt.example")
    host = await manager.register(
        SubdomainBinding(workspace_slug="01KWS", upstream_host="10.0.0.5", upstream_port=3000)
    )

    assert host == "01kws.preview.gapt.example"
    assert captured["method"] == "POST"
    assert captured["path"].endswith("/routes/...")
    assert captured["body"]["@id"] == "gapt-workspace-01kws"
    assert captured["body"]["match"][0]["host"] == ["01kws.preview.gapt.example"]
    upstream = captured["body"]["handle"][0]["upstreams"][0]["dial"]
    assert upstream == "10.0.0.5:3000"


@pytest.mark.asyncio
async def test_subdomain_manager_unregister_targets_id_path() -> None:
    seen_paths: list[str] = []

    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        seen_paths.append(path)
        return (200, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="preview.gapt.example")
    await manager.unregister("01KWS")
    assert seen_paths == ["/id/gapt-workspace-01kws"]


@pytest.mark.asyncio
async def test_subdomain_manager_unregister_idempotent_on_404() -> None:
    async def transport(method: str, path: str, body: Any | None) -> tuple[int, Any]:
        return (404, None)

    client = CaddyAdminClient(transport=transport)
    manager = SubdomainManager(client=client, preview_domain="preview.gapt.example")
    await manager.unregister("01KWS")  # must not raise
