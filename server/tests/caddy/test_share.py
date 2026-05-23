"""Share-link HMAC: issue → parse round-trip + tamper / expiry failures."""

from __future__ import annotations

import pytest

from gapt_server.domains.caddy.share import (
    ShareLinkError,
    issue_share_link,
    parse_share_link,
)


SECRET = "test-secret"


def test_round_trip_returns_workspace_id() -> None:
    token = issue_share_link(workspace_id="01KWS", secret=SECRET, ttl_s=300, now=1_000_000)
    assert parse_share_link(token, secret=SECRET, now=1_000_010) == "01KWS"


def test_parse_rejects_malformed() -> None:
    with pytest.raises(ShareLinkError) as exc:
        parse_share_link("nope", secret=SECRET)
    assert exc.value.code == "share.malformed"


def test_parse_rejects_bad_signature() -> None:
    token = issue_share_link(workspace_id="01KWS", secret=SECRET, ttl_s=300, now=1_000_000)
    # Flip the last hex char.
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(ShareLinkError) as exc:
        parse_share_link(tampered, secret=SECRET, now=1_000_010)
    assert exc.value.code == "share.bad_signature"


def test_parse_rejects_expired_token() -> None:
    token = issue_share_link(workspace_id="01KWS", secret=SECRET, ttl_s=60, now=1_000_000)
    with pytest.raises(ShareLinkError) as exc:
        parse_share_link(token, secret=SECRET, now=1_000_500)  # well past expiry
    assert exc.value.code == "share.expired"


def test_issue_rejects_non_positive_ttl() -> None:
    with pytest.raises(ShareLinkError) as exc:
        issue_share_link(workspace_id="x", secret=SECRET, ttl_s=0)
    assert exc.value.code == "share.invalid_ttl"
