"""HMAC-signed share links for preview subdomains.

`issue_share_link(workspace_id, secret, ttl_s)` returns an opaque
string like:

    {workspace_id}.{expiry_unix_ts}.{hex_signature}

`parse_share_link(token, secret)` validates the signature, checks
the expiry, and returns the workspace_id. The signature is
`hex(HMAC-SHA256(secret, f"{workspace_id}.{expiry}"))` — same
construction the WebhookTarget uses, so we keep one mental model
across the codebase.

This is *not* a session token: it's a capability URL meant for
single-link sharing of the preview iframe with someone outside the
workspace's normal ACL. The signature covers expiry, the recipient
can't extend it, and the workspace owner can revoke by rotating
`share_link_secret`."""

from __future__ import annotations

import hashlib
import hmac
import time


class ShareLinkError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_share_link(
    *, workspace_id: str, secret: str, ttl_s: int, now: float | None = None
) -> str:
    if ttl_s <= 0:
        raise ShareLinkError("share.invalid_ttl", "ttl must be > 0")
    expiry = int((now if now is not None else time.time()) + ttl_s)
    payload = f"{workspace_id}.{expiry}"
    signature = _sign(secret, payload)
    return f"{payload}.{signature}"


def parse_share_link(token: str, *, secret: str, now: float | None = None) -> str:
    parts = token.split(".")
    if len(parts) != 3:
        raise ShareLinkError("share.malformed", "share token has wrong shape")
    workspace_id, expiry_raw, signature = parts
    try:
        expiry = int(expiry_raw)
    except ValueError as exc:
        raise ShareLinkError("share.malformed", "expiry is not an integer") from exc

    payload = f"{workspace_id}.{expiry}"
    expected = _sign(secret, payload)
    # Constant-time compare so the verifier doesn't leak signature
    # bytes through timing.
    if not hmac.compare_digest(expected, signature):
        raise ShareLinkError("share.bad_signature", "share token signature did not verify")

    current = now if now is not None else time.time()
    if current > expiry:
        raise ShareLinkError("share.expired", "share token expired")
    return workspace_id
