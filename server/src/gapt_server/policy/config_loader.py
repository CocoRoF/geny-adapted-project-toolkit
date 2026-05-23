"""Policy config loader + layered override engine.

Per plan §4.5 the engine takes four sources:

  L1 Built-in default bundle (`engine._DEFAULTS`) — non-overridable
     for *INVARIANT actions* (the 5 codes below).
  L2 Server-wide YAML (`/etc/gapt/policies.yaml` or
     `GAPT_POLICY_CONFIG_PATH`).
  L3 Org-level overrides (DB `org_policies` JSONB) — future cycle.
  L4 Project-level overrides (DB `project_policies` JSONB) — future
     cycle.

Lower layers override higher layers per action. Invariant actions
can be tightened (e.g. `deploy.prod`: DENY → REQUIRE_2FA is fine,
DENY → ALLOW is *not*) but never relaxed below their floor.

Floors (most-permissive decision the action may take):

  deploy.prod       — REQUIRE_2FA
  secret.create     — REQUIRE_USER_APPROVAL
  secret.update     — REQUIRE_USER_APPROVAL
  secret.delete     — REQUIRE_USER_APPROVAL
  git.push.force    — DENY

The loader raises `PolicyConfigError` if a YAML file would violate
the floor, with the path + offending action in the message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gapt_server.policy.engine import PolicyDecision

# Most-permissive decision each invariant action may take. The loader
# refuses any override that goes *below* the floor (where lower means
# more permissive, per the natural order ALLOW < REQUIRE_USER_APPROVAL
# < REQUIRE_2FA < DENY).
INVARIANT_FLOORS: dict[str, PolicyDecision] = {
    "deploy.prod": PolicyDecision.REQUIRE_2FA,
    "secret.create": PolicyDecision.REQUIRE_USER_APPROVAL,
    "secret.update": PolicyDecision.REQUIRE_USER_APPROVAL,
    "secret.delete": PolicyDecision.REQUIRE_USER_APPROVAL,
    "git.push.force": PolicyDecision.DENY,
}

# Strictness order — higher index = more restrictive.
_STRICTNESS: dict[PolicyDecision, int] = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.REQUIRE_USER_APPROVAL: 1,
    PolicyDecision.REQUIRE_2FA: 2,
    PolicyDecision.DENY: 3,
}


class PolicyConfigError(RuntimeError):
    """Stable code suffix surfaces to the router."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PolicyOverride:
    """One parsed action → decision binding plus the source layer."""

    action: str
    decision: PolicyDecision
    source_layer: str  # "server" / "org:<id>" / "project:<id>"
    reason: str = ""


@dataclass
class PolicyOverrideSet:
    """A bag of overrides keyed by action. Supports merging with
    lower layers — later `merge_lower` calls win on collision (which
    is what the L1 < L2 < L3 < L4 precedence specifies)."""

    by_action: dict[str, PolicyOverride] = field(default_factory=dict)

    def merge_lower(self, lower: PolicyOverrideSet) -> PolicyOverrideSet:
        merged = PolicyOverrideSet(by_action=dict(self.by_action))
        for action, override in lower.by_action.items():
            merged.by_action[action] = override
        return merged


def _decision_from_str(raw: str) -> PolicyDecision:
    raw = raw.strip().lower()
    aliases = {
        "allow": PolicyDecision.ALLOW,
        "deny": PolicyDecision.DENY,
        "require_user_approval": PolicyDecision.REQUIRE_USER_APPROVAL,
        "require_user": PolicyDecision.REQUIRE_USER_APPROVAL,
        "require_2fa": PolicyDecision.REQUIRE_2FA,
        "2fa": PolicyDecision.REQUIRE_2FA,
    }
    if raw not in aliases:
        raise PolicyConfigError(
            "policy.config.bad_decision",
            f"unknown decision {raw!r}; expected one of allow / deny / "
            "require_user_approval / require_2fa",
        )
    return aliases[raw]


def check_invariant(action: str, decision: PolicyDecision) -> None:
    """Refuses any decision strictly more permissive than the
    invariant floor. Lower-stricter (e.g. REQUIRE_2FA) is fine —
    operators *tighten* via override, never relax below floor."""
    floor = INVARIANT_FLOORS.get(action)
    if floor is None:
        return
    if _STRICTNESS[decision] < _STRICTNESS[floor]:
        raise PolicyConfigError(
            "policy.config.invariant_violated",
            f"action {action!r} cannot be relaxed below {floor.value!r} "
            f"(attempted {decision.value!r})",
        )


def load_yaml(path: Path | str, *, source_layer: str = "server") -> PolicyOverrideSet:
    """Parse a policy YAML file. Returns an empty set if the file
    doesn't exist (deployments without an override file are fine)."""
    p = Path(path)
    if not p.exists():
        return PolicyOverrideSet()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PolicyConfigError(
            "policy.config.yaml_parse",
            f"could not parse {p}: {exc}",
        ) from exc
    return parse_dict(raw, source_layer=source_layer)


def parse_dict(raw: Any, *, source_layer: str) -> PolicyOverrideSet:
    """Build an override set from a dict shaped like:

        actions:
          deploy.prod: require_2fa
          git.push.protected:
            decision: allow
            reason: "we trust the local CI gate"
    """
    if not isinstance(raw, dict):
        raise PolicyConfigError(
            "policy.config.bad_root", "policy config root must be a mapping"
        )
    actions = raw.get("actions") or {}
    if not isinstance(actions, dict):
        raise PolicyConfigError(
            "policy.config.bad_actions", "`actions` must be a mapping"
        )
    out = PolicyOverrideSet()
    for action, payload in actions.items():
        if not isinstance(action, str):
            raise PolicyConfigError(
                "policy.config.bad_action_key", f"action key must be a string: {action!r}"
            )
        if isinstance(payload, str):
            decision = _decision_from_str(payload)
            reason = ""
        elif isinstance(payload, dict):
            decision_raw = payload.get("decision")
            if not isinstance(decision_raw, str):
                raise PolicyConfigError(
                    "policy.config.bad_decision",
                    f"action {action!r}: `decision` must be a string",
                )
            decision = _decision_from_str(decision_raw)
            reason_raw = payload.get("reason", "")
            reason = str(reason_raw) if reason_raw is not None else ""
        else:
            raise PolicyConfigError(
                "policy.config.bad_action_value",
                f"action {action!r}: value must be a string or a mapping",
            )
        check_invariant(action, decision)
        out.by_action[action] = PolicyOverride(
            action=action,
            decision=decision,
            source_layer=source_layer,
            reason=reason,
        )
    return out
