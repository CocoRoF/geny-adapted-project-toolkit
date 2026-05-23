"""Unbreakable invariants — `docs/09_security_authz_observability.md` §9.2.4.

PolicyEngine can be tuned by config; *these five* can't. Each helper
raises `InvariantViolation` (subclass of `RuntimeError`) — call them
from the relevant choke point so the rule is enforced *before* any
state-changing side effect.

The five rules:

1. Host docker socket / privileged path mounts on a sandbox.
2. Inserting a row that should have an `owner_id` without one.
3. Storing a secret plaintext anywhere the DB can read it.
4. Forging the actor of an audit event.
5. An agent session calling the PolicyEngine to relax itself.

Number 1 already lives in `gapt_server.domains.sandbox.backend.validate_mounts`;
the function is re-exported here as `require_safe_sandbox_mount` so
callers find them all in one place.
"""

from __future__ import annotations

import re
from typing import Any

from gapt_server.domains.sandbox.backend import (
    SecurityInvariantError,
    validate_mounts,
)


class InvariantViolation(RuntimeError):
    """Raised when a load-bearing rule from §9.2.4 is broken."""


# ───────────────────────────────────────── #1 sandbox mount ──


def require_safe_sandbox_mount(mounts: Any) -> None:
    """Wrap `validate_mounts` so callers can `from gapt_server.policy
    import require_safe_sandbox_mount` and stay self-documenting."""
    try:
        validate_mounts(tuple(mounts))
    except SecurityInvariantError as exc:
        raise InvariantViolation(str(exc)) from exc


# ───────────────────────────────────────── #2 owner_id ──


def require_owner_id(row_kind: str, owner_id: str | None) -> None:
    """Every row that has an `owner_id` column must populate it before
    insert. The DB enforces NOT NULL too — this helper produces a
    talkative error at the call site so we don't fall back to a
    generic constraint error.
    """
    if not owner_id:
        raise InvariantViolation(f"{row_kind} row requires a non-empty owner_id (§9.2.4 rule 2)")


# ───────────────────────────────────────── #3 secret plaintext ──


_SECRET_FIELDS: frozenset[str] = frozenset(
    {"value", "secret", "secret_value", "plaintext", "api_key"}
)
_OBVIOUS_TOKEN_RE = re.compile(
    r"(sk-ant-|sk-proj-|sk-[A-Za-z0-9]{16,}|ghp_|github_pat_|xox[baprs]-)"
)


def require_secret_not_in_payload(payload: Any) -> None:
    """A scan over a structure that's about to land on the DB — refuses
    any field/value that looks like a plaintext secret.

    The check is intentionally narrow: dict keys in `_SECRET_FIELDS`
    and substrings matching `_OBVIOUS_TOKEN_RE`. It exists to catch
    accidental leaks at the boundary; the masking pass in the audit
    sink handles the broader scrub.
    """
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in _SECRET_FIELDS and isinstance(v, str) and v:
                raise InvariantViolation(
                    f"refusing to persist plaintext under key {k!r} "
                    "(§9.2.4 rule 3 — use the SecretVault)"
                )
            require_secret_not_in_payload(v)
    elif isinstance(payload, list):
        for item in payload:
            require_secret_not_in_payload(item)
    elif isinstance(payload, str) and _OBVIOUS_TOKEN_RE.search(payload):
        raise InvariantViolation(
            "refusing to persist a value that looks like a secret token "
            "(§9.2.4 rule 3 — use the SecretVault)"
        )


# ───────────────────────────────────────── #4 audit forgery ──


def require_audit_event_actor(declared: str | None, observed: str | None) -> None:
    """When an audit row is written on behalf of an authenticated
    request, the `actor_id` must match the request principal — the
    code path is not allowed to put someone else's id on the row.
    `None == None` is fine (system events).
    """
    if declared != observed:
        raise InvariantViolation(
            f"audit actor mismatch: declared={declared!r} observed={observed!r} (§9.2.4 rule 4)"
        )


# ───────────────────────────────────────── #5 agent self-policy ──


def require_not_agent_session(actor_kind: str, action: str) -> None:
    """Refuse policy-management actions if the actor is an agent
    session. The PolicyEngine is administered by users / admins only.
    """
    if actor_kind == "agent_session" and action.startswith("policy."):
        raise InvariantViolation(
            f"agent_session cannot call policy management ({action!r}) (§9.2.4 rule 5)"
        )
