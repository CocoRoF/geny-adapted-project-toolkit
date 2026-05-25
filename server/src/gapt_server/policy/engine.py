"""`PolicyEngine` — default-deny gate for every state-changing action.

Built-in policy bundle for M1-E1 (one layer; project/env override
land in M1-E4):

| Action                                  | Default                  |
|-----------------------------------------|--------------------------|
| ``deploy.prod`` (LLM-initiated)         | DENY (user-only)         |
| ``deploy.dev``                          | REQUIRE_USER_APPROVAL    |
| ``deploy.staging``                      | REQUIRE_USER_APPROVAL    |
| ``secret.create``/``update``/``delete`` | DENY                     |
| ``secret.read``                         | ALLOW                    |
| ``member.invite`` / ``role.change``     | DENY                     |
| ``git.push.protected``                  | REQUIRE_USER_APPROVAL    |
| ``git.push.force``                      | DENY                     |
| ``tool.bash.danger`` (rm -rf, etc.)     | REQUIRE_USER_APPROVAL    |
| ``edit.sensitive_file`` (`.gitignore`,  | REQUIRE_USER_APPROVAL    |
| `.env*`)                                |                          |
| Anything else                           | ALLOW                    |

The decision intentionally collapses to ALLOW for unknown actions —
that matches §9.2.3's "그 외 화이트리스트 도구 → allow" and keeps the
M1 cadence moving. M1-E4's override system is what tightens unknowns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from gapt_server.domains.audit.sink import AuditSink

logger = structlog.get_logger(__name__)


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_USER_APPROVAL = "require_user_approval"
    REQUIRE_2FA = "require_2fa"


class ActorKind(StrEnum):
    USER = "user"
    AGENT_SESSION = "agent_session"
    SYSTEM = "system"


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    id: str | None = None


@dataclass(frozen=True)
class Scope:
    project_id: str | None = None
    workspace_id: str | None = None
    environment_id: str | None = None


@dataclass(frozen=True)
class PolicyEvaluation:
    action: str
    decision: PolicyDecision
    reason: str
    actor: Actor
    scope: Scope
    context: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────── built-in bundle ──


_DEFAULTS: dict[str, PolicyDecision] = {
    # Deploy. The blanket DENY on `deploy.prod` was unfair to *user*
    # clicks — it lumped a manual deploy button-press in with the
    # agent path. Agents are caught separately by
    # `_AGENT_FORBIDDEN_ACTIONS` (see below), where the kind check
    # upgrades any of these to DENY when the actor is an agent
    # session. For users the floor is REQUIRE_2FA — they still have
    # to authenticate, but the click itself isn't pre-rejected.
    "deploy.prod": PolicyDecision.REQUIRE_2FA,
    "deploy.dev": PolicyDecision.REQUIRE_USER_APPROVAL,
    "deploy.staging": PolicyDecision.REQUIRE_USER_APPROVAL,
    # Secrets
    "secret.create": PolicyDecision.DENY,
    "secret.update": PolicyDecision.DENY,
    "secret.delete": PolicyDecision.DENY,
    "secret.read": PolicyDecision.ALLOW,
    # Membership / authz
    "member.invite": PolicyDecision.DENY,
    "member.remove": PolicyDecision.DENY,
    "role.change": PolicyDecision.DENY,
    # Git
    "git.push.protected": PolicyDecision.REQUIRE_USER_APPROVAL,
    "git.push.force": PolicyDecision.DENY,
    # Risky shell + sensitive file edits
    "tool.bash.danger": PolicyDecision.REQUIRE_USER_APPROVAL,
    "edit.sensitive_file": PolicyDecision.REQUIRE_USER_APPROVAL,
}


class PolicyEngine:
    """Multi-layer policy engine.

    L1 built-in defaults (`_DEFAULTS`) merged with L2 server YAML
    overrides (`overrides`) passed at construction. L3 / L4 (org /
    project DB overrides) wrap this same instance via a future
    factory and pre-merge into `overrides` before instantiation.

    Existing callers that constructed `PolicyEngine(audit_sink=...)`
    keep working — `overrides` is opt-in.
    """

    def __init__(
        self,
        *,
        audit_sink: AuditSink | None = None,
        overrides: dict[str, PolicyDecision] | None = None,
        override_reasons: dict[str, str] | None = None,
    ) -> None:
        self._audit = audit_sink
        self._overrides: dict[str, PolicyDecision] = dict(overrides or {})
        self._override_reasons: dict[str, str] = dict(override_reasons or {})

    async def evaluate(
        self,
        *,
        action: str,
        actor: Actor,
        scope: Scope | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyEvaluation:
        scope = scope or Scope()
        context = context or {}

        if action in self._overrides:
            decision = self._overrides[action]
            reason = self._override_reasons.get(
                action, f"server override: {action} → {decision.value}"
            )
        else:
            decision = _DEFAULTS.get(action, PolicyDecision.ALLOW)
            reason = _DEFAULT_REASONS.get(action, "default bundle: action not listed → allow")

        # An agent session *can* read secrets (so the LLM picks up its
        # API keys at boot) but *cannot* mutate them. Other restrictive
        # action-classes apply equally to all actors.
        if actor.kind is ActorKind.AGENT_SESSION and action in _AGENT_FORBIDDEN_ACTIONS:
            decision = PolicyDecision.DENY
            reason = f"agent_session is never allowed to {action!r} — user-only action class"

        evaluation = PolicyEvaluation(
            action=action,
            decision=decision,
            reason=reason,
            actor=actor,
            scope=scope,
            context=context,
        )

        if self._audit is not None:
            # Avoid a circular import.
            from gapt_server.db import enums  # noqa: PLC0415
            from gapt_server.domains.audit.sink import AuditAction, AuditEvent  # noqa: PLC0415

            await self._audit.log(
                AuditEvent(
                    action=AuditAction.POLICY_EVALUATE,
                    actor_type=enums.AuditActorType(actor.kind.value),
                    actor_id=actor.id,
                    outcome=_OUTCOME_FOR[decision],
                    scope={
                        "project_id": scope.project_id,
                        "workspace_id": scope.workspace_id,
                        "environment_id": scope.environment_id,
                    },
                    subject={"action": action},
                    payload={"decision": decision.value, "reason": reason},
                )
            )
        return evaluation

    def effective_table(self) -> list[dict[str, str]]:
        """Snapshot of every known action plus its current decision +
        source. Used by `GET /api/policies` so the operator can see
        what the merged policy actually does.

        The "source" string is `builtin` for L1 defaults and
        `server` when an override is in effect. Future L3/L4 layers
        plug in by overriding this method to surface their own
        labels."""
        actions = sorted(set(_DEFAULTS.keys()) | set(self._overrides.keys()))
        out: list[dict[str, str]] = []
        for action in actions:
            if action in self._overrides:
                decision = self._overrides[action]
                source = "server"
                reason = self._override_reasons.get(action, "")
            else:
                decision = _DEFAULTS[action]
                source = "builtin"
                reason = _DEFAULT_REASONS.get(action, "")
            out.append(
                {
                    "action": action,
                    "decision": decision.value,
                    "source": source,
                    "reason": reason,
                }
            )
        return out


_AGENT_FORBIDDEN_ACTIONS: frozenset[str] = frozenset(
    {
        # The full deploy.* family + secret mutates + membership are
        # never user-initiated by an agent.
        "deploy.prod",
        "deploy.dev",
        "deploy.staging",
        "secret.create",
        "secret.update",
        "secret.delete",
        "member.invite",
        "member.remove",
        "role.change",
        "git.push.force",
    }
)


_DEFAULT_REASONS: dict[str, str] = {
    "deploy.prod": "production deploys require an explicit user click + 2FA",
    "deploy.dev": "dev deploys still need a human approval click",
    "deploy.staging": "staging deploys still need a human approval click",
    "secret.create": "secret mutations are user/UI only",
    "secret.update": "secret mutations are user/UI only",
    "secret.delete": "secret mutations are user/UI only",
    "secret.read": "secret read allowed at session boot — audited per read",
    "member.invite": "membership changes are user-only",
    "member.remove": "membership changes are user-only",
    "role.change": "role changes are user-only",
    "git.push.protected": "pushes to protected branches need user approval",
    "git.push.force": "force-push is not safe without lease semantics",
    "tool.bash.danger": "dangerous shell patterns need user approval",
    "edit.sensitive_file": "edits to gitignore / .env files need user approval",
}


# Map PolicyDecision → AuditOutcome for the audit row.
from gapt_server.db import enums as _enums  # noqa: E402  — avoids cycle at top

_OUTCOME_FOR: dict[PolicyDecision, _enums.AuditOutcome] = {
    PolicyDecision.ALLOW: _enums.AuditOutcome.OK,
    PolicyDecision.DENY: _enums.AuditOutcome.DENIED,
    PolicyDecision.REQUIRE_USER_APPROVAL: _enums.AuditOutcome.OK,
    PolicyDecision.REQUIRE_2FA: _enums.AuditOutcome.OK,
}
