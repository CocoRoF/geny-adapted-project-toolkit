"""Webhook signature + trigger policy — pure logic, no DB / HTTP."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from gapt_server.routers.webhooks import (
    _matches_trigger,
    _ref_branch,
    _verify_signature,
)


# ─── _verify_signature ───


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()


def test_signature_valid_with_prefix() -> None:
    body = b'{"ref":"refs/heads/main"}'
    assert _verify_signature("topsecret", body, _sign("topsecret", body))


def test_signature_valid_bare_hex() -> None:
    """We accept signatures without the `sha256=` prefix too — GitHub
    requires it but local test tooling sometimes omits it."""
    body = b'{}'
    bare = hmac.new(b"x", body, hashlib.sha256).hexdigest()
    assert _verify_signature("x", body, bare)


def test_signature_wrong_secret_rejected() -> None:
    body = b'{"a":1}'
    assert not _verify_signature("real", body, _sign("fake", body))


def test_signature_tampered_body_rejected() -> None:
    body = b'{"a":1}'
    tampered = b'{"a":2}'
    assert not _verify_signature("k", tampered, _sign("k", body))


def test_signature_missing_header_rejected() -> None:
    assert not _verify_signature("k", b"x", None)
    assert not _verify_signature("k", b"x", "")


@pytest.mark.parametrize(
    "header",
    ["sha256=", "not-hex", "sha256=garbage", "sha256=abcdef"],
)
def test_signature_malformed_header_rejected(header: str) -> None:
    assert not _verify_signature("k", b"body", header)


# ─── _ref_branch ───


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("refs/heads/main", "main"),
        ("refs/heads/feature/x", "feature/x"),
        ("refs/tags/v1", None),  # tags don't trigger
        ("refs/heads/", ""),  # malformed but parseable
        (None, None),
        ("", None),
        (12345, None),  # non-string
    ],
)
def test_ref_branch_extraction(ref: object, expected: str | None) -> None:
    assert _ref_branch(ref) == expected  # type: ignore[arg-type]


# ─── _matches_trigger ───


def test_trigger_match_explicit_branch() -> None:
    cfg = {"trigger": {"branch": "main"}}
    assert _matches_trigger(cfg, "main")


def test_trigger_no_match_for_different_branch() -> None:
    cfg = {"trigger": {"branch": "main"}}
    assert not _matches_trigger(cfg, "feature/x")


def test_trigger_missing_trigger_block_means_manual_only() -> None:
    """The default — no trigger key, no auto-deploy. Webhook
    enabling alone shouldn't accidentally fire."""
    assert not _matches_trigger({}, "main")
    assert not _matches_trigger({"compose_path": "x.yml"}, "main")


def test_trigger_malformed_block_does_not_crash() -> None:
    assert not _matches_trigger({"trigger": "main"}, "main")  # type: ignore[arg-type]
    assert not _matches_trigger({"trigger": {"branch": 12}}, "main")  # type: ignore[arg-type]
