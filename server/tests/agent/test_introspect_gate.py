"""ASGI bearer gate for the self-introspection MCP.

The token mint/verify scheme is covered in ``test_introspect_mcp.py``;
this file exercises the *gate* — the ``_BearerWrapper`` ASGI shim that
``build_introspect_app`` returns. We drive the real wrapped app over an
in-process httpx ASGITransport and assert that:

  * a request with no Authorization header is rejected (401),
  * a ``Bearer <garbage>`` token is rejected (401),
  * an expired but well-formed token is rejected (401), and
  * a VALID token *passes the gate* — it may still fail MCP protocol
    negotiation (400/406), but it must NOT be a 401.

``build_introspect_app`` only needs the container at BUILD time as a
captured closure value; the single attribute the wrapper dereferences
on the request path is ``container.settings.session_secret``. So a
``SimpleNamespace`` stub with just ``settings.session_secret`` is
enough to construct and drive the gate without a DB or services.
"""

from __future__ import annotations

import types

import httpx
import pytest

from gapt_server.agent.introspect_mcp import (
    build_introspect_app,
    mint_introspect_token,
)

_SECRET = "gate-test-secret"
_MCP_PATH = "/mcp"


def _stub_container() -> types.SimpleNamespace:
    """Minimal stand-in for ``AppContainer``.

    Only ``settings.session_secret`` is touched on the request path
    (inside ``_BearerWrapper.__call__``); the rest of the container is
    referenced solely from within the MCP tool closures, which the gate
    tests never invoke."""
    return types.SimpleNamespace(
        settings=types.SimpleNamespace(session_secret=_SECRET),
    )


async def _post(app: object, headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=transport, base_url="http://introspect.test"
    ) as client:
        return await client.post(
            _MCP_PATH,
            headers={
                # A real MCP client sends these; supplying them means a
                # VALID token gets past content negotiation to the
                # session layer rather than tripping on missing Accept.
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                **headers,
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )


@pytest.mark.asyncio
async def test_gate_rejects_missing_authorization() -> None:
    app = build_introspect_app(_stub_container())
    resp = await _post(app, headers={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gate_rejects_bearer_garbage() -> None:
    app = build_introspect_app(_stub_container())
    resp = await _post(app, headers={"authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gate_rejects_non_bearer_scheme() -> None:
    app = build_introspect_app(_stub_container())
    # Right secret, valid-looking value, wrong scheme: still no entry.
    token = mint_introspect_token(
        workspace_id="w1", project_id="p1", secret=_SECRET
    )
    resp = await _post(app, headers={"authorization": f"Basic {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gate_rejects_expired_token() -> None:
    app = build_introspect_app(_stub_container())
    expired = mint_introspect_token(
        workspace_id="w1", project_id="p1", secret=_SECRET, ttl_s=-1
    )
    resp = await _post(app, headers={"authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gate_passes_valid_token() -> None:
    app = build_introspect_app(_stub_container())
    token = mint_introspect_token(
        workspace_id="w1", project_id="p1", secret=_SECRET
    )
    # The inner FastMCP session manager needs its task group running
    # (normally started by create_app's lifespan) before it can serve a
    # request — otherwise a gate-passing call raises rather than
    # returning an HTTP status. Run it so the valid token reaches a real
    # response and we can assert the gate (not the transport) decided.
    async with app.session_manager.run():  # type: ignore[attr-defined]
        resp = await _post(app, headers={"authorization": f"Bearer {token}"})
    # A valid token clears the bearer gate. The request may still fail
    # MCP protocol negotiation downstream (e.g. 400/406 on the session
    # handshake), but the gate itself must not be the rejector.
    assert resp.status_code != 401
