"""CloudflareClient — HTTP surface with httpx MockTransport.

Pattern mirrors `tests/auth/test_github_oauth.py` — inject a
factory that returns an `AsyncClient` wrapping a `MockTransport`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from gapt_server.domains.providers.cloudflare.client import (
    CLOUDFLARE_API_BASE,
    CloudflareApiError,
    CloudflareClient,
)


# ──────────────────────────────────────────── helpers ──


def _ok(result: Any) -> httpx.Response:
    return httpx.Response(
        200, json={"success": True, "errors": [], "messages": [], "result": result}
    )


def _err(
    status: int,
    code: int = 9001,
    message: str = "boom",
) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "success": False,
            "errors": [{"code": code, "message": message}],
            "messages": [],
            "result": None,
        },
    )


def _client(handler: callable) -> CloudflareClient:
    return CloudflareClient(
        token="cf-test-token",
        client_factory=lambda _timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )


# ──────────────────────────────────────────── happy-path ──


@pytest.mark.asyncio
async def test_verify_token_ok() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return _ok({"id": "tok-1", "status": "active"})

    out = await _client(handler).verify_token()
    assert out == {"id": "tok-1", "status": "active"}
    assert captured["url"] == f"{CLOUDFLARE_API_BASE}/user/tokens/verify"
    assert captured["auth"] == "Bearer cf-test-token"


@pytest.mark.asyncio
async def test_list_accounts_passes_per_page() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return _ok([{"id": "acc-1", "name": "n"}])

    out = await _client(handler).list_accounts()
    assert out == [{"id": "acc-1", "name": "n"}]
    assert "per_page=50" in captured["url"]


@pytest.mark.asyncio
async def test_list_zones_filters_by_account() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return _ok([{"id": "zone-1", "name": "example.com"}])

    await _client(handler).list_zones(account_id="acc-1")
    # The "account.id" query param is URL-encoded as "account.id=acc-1".
    assert "account.id=acc-1" in captured["url"]


@pytest.mark.asyncio
async def test_get_tunnel_configuration() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert "/accounts/aid/cfd_tunnel/tid/configurations" in str(req.url)
        return _ok({"source": "cloudflare", "version": 2, "config": {"ingress": []}})

    out = await _client(handler).get_tunnel_configuration("aid", "tid")
    assert out["source"] == "cloudflare"


@pytest.mark.asyncio
async def test_put_tunnel_configuration_wraps_in_config_envelope() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content.decode())
        return _ok({"updated": True})

    await _client(handler).put_tunnel_configuration(
        "aid", "tid", config={"ingress": [{"hostname": "*.x", "service": "http://o"}]}
    )
    # The PUT body must wrap the config dict — the Cloudflare API
    # rejects flat `{"ingress": [...]}` without the outer `config`.
    assert captured["body"] == {
        "config": {"ingress": [{"hostname": "*.x", "service": "http://o"}]}
    }


@pytest.mark.asyncio
async def test_create_dns_record_payload_shape() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content.decode())
        return _ok({"id": "rec-1"})

    await _client(handler).create_dns_record(
        "zid",
        type="CNAME",
        name="*.example.com",
        content="<uuid>.cfargotunnel.com",
        proxied=True,
        comment="GAPT auto",
    )
    assert captured["body"] == {
        "type": "CNAME",
        "name": "*.example.com",
        "content": "<uuid>.cfargotunnel.com",
        "proxied": True,
        "ttl": 1,
        "comment": "GAPT auto",
    }


@pytest.mark.asyncio
async def test_enable_total_tls_sends_certificate_authority() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content.decode())
        captured["method"] = req.method
        return _ok({"enabled": True})

    await _client(handler).enable_total_tls(
        "zid", certificate_authority="lets_encrypt"
    )
    assert captured["method"] == "PATCH"
    assert captured["body"] == {
        "enabled": True,
        "certificate_authority": "lets_encrypt",
    }


# ──────────────────────────────────────────── error paths ──


@pytest.mark.asyncio
async def test_api_returns_success_false_raises_with_errors() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": False,
                "errors": [{"code": 6111, "message": "Invalid Authorization"}],
                "messages": [],
                "result": None,
            },
        )

    with pytest.raises(CloudflareApiError) as ei:
        await _client(handler).verify_token()
    assert "Invalid Authorization" in str(ei.value)
    assert ei.value.errors[0]["code"] == 6111


@pytest.mark.asyncio
async def test_http_error_propagates_status() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _err(403, code=9106, message="Authentication failed")

    with pytest.raises(CloudflareApiError) as ei:
        await _client(handler).list_zones()
    assert ei.value.status == 403


@pytest.mark.asyncio
async def test_non_json_response_raises_with_status() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"<html>bad gateway</html>")

    with pytest.raises(CloudflareApiError) as ei:
        await _client(handler).verify_token()
    assert ei.value.status == 502
    assert "non-JSON" in str(ei.value)
