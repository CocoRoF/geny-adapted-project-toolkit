"""CloudflareService — orchestration on top of CloudflareClient.

Focuses on the *logic* that lives outside the HTTP layer:
- `infer_tunnel_mode` heuristic
- account derivation from zones when /accounts is empty (the
  "token has no Account:Account Settings:Read" case that bit us
  during Phase B integration)
- `ensure_wildcard_ingress` idempotency + catch-all preservation
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from gapt_server.domains.providers.cloudflare.client import (
    CloudflareApiError,
    CloudflareClient,
)
from gapt_server.domains.providers.cloudflare.service import (
    CloudflareService,
    IngressEntry,
    infer_tunnel_mode,
)


# ──────────────────────────────────────────── infer_tunnel_mode ──


class TestInferTunnelMode:
    """Heuristic for telling local-config vs remote-managed tunnels
    apart from the `/configurations` response shape."""

    def test_explicit_source_cloudflare(self) -> None:
        assert infer_tunnel_mode({"source": "cloudflare"}) == "remote_managed"

    def test_explicit_source_local(self) -> None:
        assert infer_tunnel_mode({"source": "local"}) == "local_config"

    def test_version_gt_zero_implies_remote(self) -> None:
        body = {"version": 5, "config": {"ingress": [{"service": "http://x"}]}}
        assert infer_tunnel_mode(body) == "remote_managed"

    def test_version_zero_with_default_catchall_implies_local(self) -> None:
        body = {"version": 0, "config": {"ingress": [{"service": "http_status:404"}]}}
        assert infer_tunnel_mode(body) == "local_config"

    def test_version_zero_empty_ingress_implies_local(self) -> None:
        assert infer_tunnel_mode({"version": 0, "config": {"ingress": []}}) == "local_config"

    def test_missing_fields_unknown(self) -> None:
        assert infer_tunnel_mode({}) == "unknown"


# ──────────────────────────────────────── helpers ──


def _mock_client(handler: callable) -> CloudflareClient:
    return CloudflareClient(
        token="tok",
        client_factory=lambda _t: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _ok(result: Any) -> httpx.Response:
    return httpx.Response(
        200, json={"success": True, "errors": [], "messages": [], "result": result}
    )


# ──────────────────────── verify_and_discover ──


@pytest.mark.asyncio
async def test_verify_and_discover_derives_accounts_from_zones() -> None:
    """The bug from Phase B integration: token has Cloudflare
    Tunnel:Edit but NOT Account:Account Settings:Read, so
    `/accounts` returns empty even though tunnels are reachable.
    The service must derive accounts from zone ownership."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/client/v4/user/tokens/verify":
            return _ok({"id": "t", "status": "active"})
        if path == "/client/v4/accounts":
            return _ok([])  # ← empty, the bug-triggering case
        if path == "/client/v4/zones":
            return _ok(
                [
                    {
                        "id": "zid",
                        "name": "example.com",
                        "account": {"id": "derived-aid", "name": "MyAccount"},
                    }
                ]
            )
        if "/cfd_tunnel" in path:
            return _ok(
                [{"id": "tid", "name": "t1", "status": "healthy", "connections": [1, 2]}]
            )
        return httpx.Response(404, json={"success": False, "errors": [], "result": None})

    svc = CloudflareService(_mock_client(handler))
    out = await svc.verify_and_discover()

    assert out["accounts"] == [
        {"id": "derived-aid", "name": "MyAccount", "source": "zone"}
    ]
    assert out["tunnels_by_account"]["derived-aid"][0]["id"] == "tid"
    assert out["tunnels_by_account"]["derived-aid"][0]["connections"] == 2
    # Warnings should be empty when tunnel listing actually works —
    # we don't want false alarms for the "informational only" case.
    assert out["warnings"] == []


@pytest.mark.asyncio
async def test_verify_and_discover_warns_when_nothing_visible() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/client/v4/user/tokens/verify":
            return _ok({"id": "t"})
        if req.url.path in ("/client/v4/accounts", "/client/v4/zones"):
            return _ok([])
        return httpx.Response(404, json={"success": False, "result": None})

    out = await CloudflareService(_mock_client(handler)).verify_and_discover()
    assert any("no accounts or zones" in w for w in out["warnings"])


@pytest.mark.asyncio
async def test_verify_warns_when_tunnel_listing_403() -> None:
    """Token sees an account (or a zone-derived one) but can't list
    tunnels — should be flagged with a scope-missing hint."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/client/v4/user/tokens/verify":
            return _ok({"id": "t"})
        if req.url.path == "/client/v4/accounts":
            return _ok([{"id": "aid", "name": "n"}])
        if req.url.path == "/client/v4/zones":
            return _ok([])
        if "/cfd_tunnel" in req.url.path:
            return httpx.Response(
                403,
                json={
                    "success": False,
                    "errors": [{"code": 9106, "message": "forbidden"}],
                    "result": None,
                },
            )
        return httpx.Response(404, json={"success": False, "result": None})

    out = await CloudflareService(_mock_client(handler)).verify_and_discover()
    assert any("tunnel listing failed" in w for w in out["warnings"])


# ────────────────────────── snapshot + ensure_wildcard ──


def _config_response(ingress: list[dict[str, Any]], source: str = "cloudflare") -> httpx.Response:
    return _ok({"source": source, "version": 1, "config": {"ingress": ingress}})


@pytest.mark.asyncio
async def test_snapshot_parses_ingress_and_mode() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _config_response(
            [
                {"hostname": "a.example", "service": "http://o:1"},
                {"service": "http_status:404"},
            ]
        )

    snap = await CloudflareService(_mock_client(handler)).snapshot("aid", "tid")
    assert snap.mode == "remote_managed"
    assert len(snap.ingress) == 2
    assert snap.ingress[0].hostname == "a.example"


@pytest.mark.asyncio
async def test_ensure_wildcard_rejects_local_config_mode() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _config_response(
            [{"service": "http_status:404"}], source="local"
        )

    svc = CloudflareService(_mock_client(handler))
    with pytest.raises(CloudflareApiError) as ei:
        await svc.ensure_wildcard_ingress(
            "aid", "tid", wildcard_hostname="*.x", upstream="http://o"
        )
    assert ei.value.status == 409
    assert "local-config" in str(ei.value)


@pytest.mark.asyncio
async def test_ensure_wildcard_inserts_before_catchall() -> None:
    """First call: ingress has only the catch-all. After ensure,
    the wildcard sits immediately before it (Cloudflare requires
    catch-all to be last)."""
    state: dict[str, Any] = {
        "ingress": [{"service": "http_status:404"}],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return _config_response(state["ingress"])
        if req.method == "PUT":
            import json as _j

            body = _j.loads(req.content.decode())
            state["ingress"] = body["config"]["ingress"]
            return _ok({"updated": True})
        return httpx.Response(404, json={"success": False, "result": None})

    svc = CloudflareService(_mock_client(handler))
    snap = await svc.ensure_wildcard_ingress(
        "aid", "tid", wildcard_hostname="*.preview.x", upstream="http://upstream"
    )
    # After upsert: [wildcard, catch-all]
    assert len(snap.ingress) == 2
    assert snap.ingress[0].hostname == "*.preview.x"
    assert snap.ingress[0].service == "http://upstream"
    assert not snap.ingress[1].hostname  # catch-all has empty hostname


@pytest.mark.asyncio
async def test_ensure_wildcard_is_idempotent() -> None:
    """Wildcard already present + same upstream → no duplicate."""
    existing = [
        {"hostname": "*.preview.x", "service": "http://upstream"},
        {"service": "http_status:404"},
    ]
    state: dict[str, Any] = {"ingress": list(existing)}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return _config_response(state["ingress"])
        if req.method == "PUT":
            import json as _j

            state["ingress"] = _j.loads(req.content.decode())["config"]["ingress"]
            return _ok({"updated": True})
        return httpx.Response(404, json={"success": False, "result": None})

    svc = CloudflareService(_mock_client(handler))
    snap = await svc.ensure_wildcard_ingress(
        "aid", "tid", wildcard_hostname="*.preview.x", upstream="http://upstream"
    )
    # Still exactly the same 2 entries — no duplicate wildcard appended.
    assert len(snap.ingress) == 2
    assert snap.ingress[0].hostname == "*.preview.x"


@pytest.mark.asyncio
async def test_ensure_wildcard_updates_existing_upstream() -> None:
    """Wildcard present but pointing at the wrong upstream → replace
    in-place (don't add a second one)."""
    state: dict[str, Any] = {
        "ingress": [
            {"hostname": "*.preview.x", "service": "http://OLD"},
            {"service": "http_status:404"},
        ],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return _config_response(state["ingress"])
        if req.method == "PUT":
            import json as _j

            state["ingress"] = _j.loads(req.content.decode())["config"]["ingress"]
            return _ok({"updated": True})
        return httpx.Response(404, json={"success": False, "result": None})

    svc = CloudflareService(_mock_client(handler))
    snap = await svc.ensure_wildcard_ingress(
        "aid", "tid", wildcard_hostname="*.preview.x", upstream="http://NEW"
    )
    assert len(snap.ingress) == 2
    assert snap.ingress[0].service == "http://NEW"
