"""Self-introspection MCP — token scheme + bearer gate."""

from __future__ import annotations

from gapt_server.agent.introspect_mcp import (
    mint_introspect_token,
    verify_introspect_token,
)

_SECRET = "test-secret"


def test_token_roundtrip_carries_scope() -> None:
    token = mint_introspect_token(
        workspace_id="w1", project_id="p1", secret=_SECRET
    )
    assert verify_introspect_token(token, secret=_SECRET) == ("w1", "p1")


def test_token_rejects_wrong_secret() -> None:
    token = mint_introspect_token(workspace_id="w1", project_id="p1", secret=_SECRET)
    assert verify_introspect_token(token, secret="other") is None


def test_token_rejects_tampered_payload() -> None:
    token = mint_introspect_token(workspace_id="w1", project_id="p1", secret=_SECRET)
    payload, sig = token.split(".")
    # Flip a char in the payload — signature must no longer match.
    forged = ("A" if payload[0] != "A" else "B") + payload[1:]
    assert verify_introspect_token(f"{forged}.{sig}", secret=_SECRET) is None


def test_token_rejects_expired() -> None:
    token = mint_introspect_token(
        workspace_id="w1", project_id="p1", secret=_SECRET, ttl_s=-1
    )
    assert verify_introspect_token(token, secret=_SECRET) is None
    # Sanity: a healthy TTL is in the future.
    fresh = mint_introspect_token(workspace_id="w1", project_id="p1", secret=_SECRET)
    assert verify_introspect_token(fresh, secret=_SECRET) is not None


def test_token_rejects_garbage() -> None:
    for bad in ("", "x", "a.b", "not-a-token-at-all", "  "):
        assert verify_introspect_token(bad, secret=_SECRET) is None
