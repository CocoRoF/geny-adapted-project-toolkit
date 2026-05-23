"""PolicyEngine skeleton — Cycle 1.10.

Built-in default bundle implements the table in
`docs/09_security_authz_observability.md` §9.2.3. Override layers
(server / org / project) land in M1-E4.

`policy.invariants` encodes the *unbreakable* rules from §9.2.4 — five
items that the policy engine cannot relax under any configuration.
The mount-whitelist part of those invariants already lives in
`gapt_server.domains.sandbox.backend.validate_mounts`; this package
re-exports it as a one-stop hub.
"""

from gapt_server.policy.engine import (
    Actor,
    ActorKind,
    PolicyDecision,
    PolicyEngine,
    PolicyEvaluation,
    Scope,
)
from gapt_server.policy.invariants import (
    InvariantViolation,
    require_audit_event_actor,
    require_not_agent_session,
    require_owner_id,
    require_safe_sandbox_mount,
    require_secret_not_in_payload,
)

__all__ = [
    "Actor",
    "ActorKind",
    "InvariantViolation",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyEvaluation",
    "Scope",
    "require_audit_event_actor",
    "require_not_agent_session",
    "require_owner_id",
    "require_safe_sandbox_mount",
    "require_secret_not_in_payload",
]
