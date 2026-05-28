"""Thin async Cloudflare API client.

Covers exactly the surface the GAPT provider integration needs:

- Token verification + scope discovery (`GET /user/tokens/verify`).
- Account enumeration (`GET /accounts`).
- Zone enumeration (`GET /zones`).
- Cloudflare Tunnel ("cfd_tunnel") enumeration (`GET /accounts/{aid}/cfd_tunnel`).
- Tunnel remote configuration get/put
  (`GET|PUT /accounts/{aid}/cfd_tunnel/{tid}/configurations`).

The client deliberately stays small — no SDK pinning, no schemas
generated from an OpenAPI spec, no retry policy beyond HTTP-level
errors surfaced as `CloudflareApiError`. Higher-level orchestration
(e.g. "ensure a wildcard ingress entry exists") lives in
`service.py`.

All methods return parsed JSON dicts. Errors raise
`CloudflareApiError` carrying the upstream `errors` array verbatim
so the caller can surface specific messages
(e.g. token scope missing).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"


def _default_client_factory(timeout_s: float) -> httpx.AsyncClient:
    """Production factory — fresh `httpx.AsyncClient` per request."""
    return httpx.AsyncClient(timeout=timeout_s)


class CloudflareApiError(RuntimeError):
    """Raised when Cloudflare returns `success: false` or a non-2xx
    HTTP status. `errors` mirrors the upstream array — list of
    `{code, message, ...}` dicts. `status` is the HTTP code."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.errors = errors or []


class CloudflareClient:
    """Async Cloudflare API wrapper. Bearer-token auth. One client
    instance is meant to live for a single request — construct it
    fresh from a token, run a few calls, drop it.

    Token requirements (minimum for GAPT's use cases):
    - `Account:Cloudflare Tunnel:Edit` — to list + reconfigure tunnels.
    - `Zone:Zone:Read` + `Zone:DNS:Edit` (optional, only if we add
      DNS auto-create later).

    The token-verify endpoint reports `not_before`/`expires_on` and
    the active state but does **not** return the granular scope list
    on the public API — callers should attempt the actual operation
    and surface `errors[]` when permission is missing."""

    def __init__(
        self,
        token: str,
        *,
        timeout_s: float = 10.0,
        base_url: str = CLOUDFLARE_API_BASE,
        client_factory: Callable[[float], httpx.AsyncClient] | None = None,
    ) -> None:
        self._token = token
        self._timeout = timeout_s
        self._base = base_url.rstrip("/")
        # Test seam — pass a factory that returns `httpx.AsyncClient
        # (transport=httpx.MockTransport(...))` to swap out the
        # network for a fixture without touching production code.
        self._client_factory = client_factory or _default_client_factory

    # ───────────────────────────────────────── low-level transport ──

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        async with self._client_factory(self._timeout) as c:
            resp = await c.request(
                method, url, headers=headers, json=json_body, params=params
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise CloudflareApiError(
                f"Cloudflare API returned non-JSON body (HTTP {resp.status_code})",
                status=resp.status_code,
            ) from exc

        if resp.status_code >= 400 or body.get("success") is False:
            errs = body.get("errors") or []
            first = errs[0].get("message") if errs and isinstance(errs[0], dict) else ""
            raise CloudflareApiError(
                first or f"Cloudflare API error (HTTP {resp.status_code})",
                status=resp.status_code,
                errors=errs,
            )
        return body

    # ───────────────────────────────────────── auth / discovery ──

    async def verify_token(self) -> dict[str, Any]:
        """`GET /user/tokens/verify`. Returns `{id, status, not_before,
        expires_on}` on success. Raises on invalid token."""
        body = await self._request("GET", "/user/tokens/verify")
        return body.get("result") or {}

    async def list_accounts(self) -> list[dict[str, Any]]:
        """`GET /accounts`. Returns the accounts the token can see —
        usually exactly one when the token was created from a single
        account's UI."""
        body = await self._request("GET", "/accounts", params={"per_page": 50})
        return body.get("result") or []

    async def list_zones(self, account_id: str | None = None) -> list[dict[str, Any]]:
        """`GET /zones`. Optionally filtered to an account. Returns
        `[{id, name, status, account.id, ...}]`."""
        params: dict[str, Any] = {"per_page": 50}
        if account_id:
            params["account.id"] = account_id
        body = await self._request("GET", "/zones", params=params)
        return body.get("result") or []

    # ───────────────────────────────────────── tunnels ──

    async def list_tunnels(self, account_id: str) -> list[dict[str, Any]]:
        """`GET /accounts/{aid}/cfd_tunnel?is_deleted=false`.
        Returns the live tunnels for the account."""
        body = await self._request(
            "GET",
            f"/accounts/{account_id}/cfd_tunnel",
            params={"is_deleted": "false", "per_page": 50},
        )
        return body.get("result") or []

    async def get_tunnel_configuration(
        self, account_id: str, tunnel_id: str
    ) -> dict[str, Any]:
        """`GET /accounts/{aid}/cfd_tunnel/{tid}/configurations`.

        For **remote-managed** tunnels the `config.ingress` array is
        the authoritative routing table.

        For **local-config** tunnels Cloudflare typically returns the
        last-known remote config (often empty / a one-entry
        `http_status:404` default), because the actual ingress lives
        in `~/.cloudflared/config.yml` on the host and never round-
        trips through this endpoint. We use the shape of the
        response to *guess* mode — see `infer_tunnel_mode()` in
        `service.py`."""
        body = await self._request(
            "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
        )
        return body.get("result") or {}

    async def put_tunnel_configuration(
        self,
        account_id: str,
        tunnel_id: str,
        *,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """`PUT /accounts/{aid}/cfd_tunnel/{tid}/configurations`.

        Body is `{"config": {"ingress": [...], "warp-routing": {...}}}`.
        Only takes effect when the tunnel runs in remote-managed mode;
        for local-config tunnels the new value is silently ignored at
        runtime."""
        body = await self._request(
            "PUT",
            f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            json_body={"config": config},
        )
        return body.get("result") or {}

    # ─────────────────────────────────────── SSL / certificates ──

    async def list_certificate_packs(
        self, zone_id: str
    ) -> list[dict[str, Any]]:
        """`GET /zones/{zid}/ssl/certificate_packs`. Returns active
        certificate packs — used to detect whether a wildcard cert
        is already issued for the zone."""
        body = await self._request(
            "GET",
            f"/zones/{zone_id}/ssl/certificate_packs",
            params={"status": "all", "per_page": 50},
        )
        return body.get("result") or []

    async def get_total_tls(self, zone_id: str) -> dict[str, Any]:
        """`GET /zones/{zid}/acm/total_tls`. Returns the current
        Total TLS configuration. `{enabled: bool, certificate_authority}`."""
        body = await self._request(
            "GET", f"/zones/{zone_id}/acm/total_tls"
        )
        return body.get("result") or {}

    async def list_dns_records(
        self, zone_id: str, *, name: str | None = None
    ) -> list[dict[str, Any]]:
        """`GET /zones/{zid}/dns_records`. Optionally filtered by
        exact name (e.g. `*.hrletsgo.me`) so we can probe before
        creating."""
        params: dict[str, Any] = {"per_page": 50}
        if name:
            params["name"] = name
        body = await self._request(
            "GET", f"/zones/{zone_id}/dns_records", params=params
        )
        return body.get("result") or []

    async def create_dns_record(
        self,
        zone_id: str,
        *,
        type: str,
        name: str,
        content: str,
        proxied: bool = True,
        ttl: int = 1,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """`POST /zones/{zid}/dns_records`. Creates a DNS record —
        needs `Zone:DNS:Edit` scope. `ttl=1` means "auto", which is
        Cloudflare's default and required when `proxied=True`."""
        json_body: dict[str, Any] = {
            "type": type,
            "name": name,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        }
        if comment is not None:
            json_body["comment"] = comment
        body = await self._request(
            "POST", f"/zones/{zone_id}/dns_records", json_body=json_body
        )
        return body.get("result") or {}

    async def enable_total_tls(
        self, zone_id: str, *, certificate_authority: str = "google"
    ) -> dict[str, Any]:
        """`PATCH /zones/{zid}/acm/total_tls` with `enabled=true`.

        Total TLS is Cloudflare's free feature that auto-issues a
        cert for every subdomain in the zone — exactly what we need
        for `*.<preview-domain>` to handshake successfully. Available
        on all plans (including Free); `certificate_authority` picks
        the issuing CA ("lets_encrypt", "google", or "ssl_com").

        Token must have `Zone:SSL and Certificates:Edit`."""
        body = await self._request(
            "PATCH",
            f"/zones/{zone_id}/acm/total_tls",
            json_body={
                "enabled": True,
                "certificate_authority": certificate_authority,
            },
        )
        return body.get("result") or {}
