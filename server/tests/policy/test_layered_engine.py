"""PolicyEngine with L2 server-YAML overrides — evaluation + table."""

from __future__ import annotations

import pytest

from gapt_server.policy.engine import (
    Actor,
    ActorKind,
    PolicyDecision,
    PolicyEngine,
)


@pytest.mark.asyncio
async def test_override_takes_precedence_over_builtin() -> None:
    engine = PolicyEngine(
        overrides={"git.push.protected": PolicyDecision.ALLOW},
        override_reasons={"git.push.protected": "trust local CI"},
    )
    result = await engine.evaluate(
        action="git.push.protected",
        actor=Actor(kind=ActorKind.USER, id="u1"),
    )
    assert result.decision is PolicyDecision.ALLOW
    assert "trust local CI" in result.reason


@pytest.mark.asyncio
async def test_builtin_fallback_when_no_override() -> None:
    engine = PolicyEngine(overrides={"git.push.protected": PolicyDecision.ALLOW})
    result = await engine.evaluate(
        action="secret.create",
        actor=Actor(kind=ActorKind.USER, id="u1"),
    )
    # secret.create has a builtin DENY for everyone — override
    # doesn't touch it.
    assert result.decision is PolicyDecision.DENY


@pytest.mark.asyncio
async def test_agent_forbidden_still_holds_over_override() -> None:
    # Even if the operator overrides deploy.prod, an agent-actor
    # still gets DENY because deploy.prod is in
    # _AGENT_FORBIDDEN_ACTIONS.
    engine = PolicyEngine(overrides={"deploy.prod": PolicyDecision.REQUIRE_2FA})
    result = await engine.evaluate(
        action="deploy.prod",
        actor=Actor(kind=ActorKind.AGENT_SESSION, id="s1"),
    )
    assert result.decision is PolicyDecision.DENY


def test_effective_table_reports_source_layer() -> None:
    engine = PolicyEngine(
        overrides={"git.push.protected": PolicyDecision.DENY},
        override_reasons={"git.push.protected": "tighter than builtin"},
    )
    table = engine.effective_table()
    rows_by_action = {row["action"]: row for row in table}
    assert rows_by_action["git.push.protected"]["source"] == "server"
    assert rows_by_action["git.push.protected"]["decision"] == "deny"
    # An unaltered builtin keeps its source label.
    assert rows_by_action["secret.create"]["source"] == "builtin"
