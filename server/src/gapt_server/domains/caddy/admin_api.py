"""Caddy admin-API client.

Talks JSON-over-HTTP to Caddy's admin endpoint (default
`http://caddy:2019`). We expose a minimal verb set that the
subdomain manager needs:

- `get(path)` — read a config slice.
- `put(path, body)` — replace a config slice.
- `delete(path)` — drop a config slice.
- `post(path, body)` — append to an array slice.

The transport is pluggable so tests inject a stub instead of an
httpx client. The default `CaddyHttpTransport` uses `httpx.AsyncClient`
with a tight timeout (Caddy admin operations are local and should
complete in milliseconds; a slow response usually means Caddy is
unhealthy and we should fail fast).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx


class CaddyAdminError(RuntimeError):
    """Carries a stable exec.*.* code suffix."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# `(method, path, body | None) -> (status, body)`.
# `body` for the response is the JSON-parsed payload (or `None` for
# 200/204 with empty body); we keep it open-typed so the admin client
# can drive both single-object and array slices.
CaddyTransport = Callable[[str, str, Any | None], Awaitable[tuple[int, Any]]]


@dataclass
class CaddyHttpTransport:
    """Real transport. Tests use a hand-rolled async callable."""

    base_url: str
    timeout_s: float = 5.0

    async def __call__(
        self, method: str, path: str, body: Any | None
    ) -> tuple[int, Any]:
        url = self.base_url.rstrip("/") + path
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.request(
                method,
                url,
                json=body,
                headers={"Accept": "application/json"},
            )
        if resp.status_code >= 500:
            raise CaddyAdminError(
                "caddy.admin.server_error",
                f"caddy admin {method} {path} returned {resp.status_code}",
            )
        if resp.status_code in (200, 201):
            try:
                return resp.status_code, resp.json()
            except ValueError:
                return resp.status_code, None
        return resp.status_code, None


@dataclass
class CaddyAdminClient:
    """Thin wrapper over the transport. The verbs map 1:1 to Caddy's
    admin API surface so the subdomain manager can build atomic
    config patches."""

    transport: CaddyTransport
    _: int = field(default=0, init=False, repr=False)

    async def get(self, path: str) -> Any:
        status, body = await self.transport("GET", path, None)
        if status == 404:
            return None
        if status >= 400:
            raise CaddyAdminError(
                "caddy.admin.get_failed",
                f"GET {path} -> {status}",
            )
        return body

    async def put(self, path: str, body: Any) -> None:
        status, _ = await self.transport("PUT", path, body)
        if status >= 400:
            raise CaddyAdminError(
                "caddy.admin.put_failed",
                f"PUT {path} -> {status}",
            )

    async def post(self, path: str, body: Any) -> None:
        status, _ = await self.transport("POST", path, body)
        if status >= 400:
            raise CaddyAdminError(
                "caddy.admin.post_failed",
                f"POST {path} -> {status}",
            )

    async def delete(self, path: str) -> None:
        status, _ = await self.transport("DELETE", path, None)
        if status >= 400 and status != 404:
            raise CaddyAdminError(
                "caddy.admin.delete_failed",
                f"DELETE {path} -> {status}",
            )
