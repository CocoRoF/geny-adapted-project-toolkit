"""PolicyEngine default-bundle decisions."""

from __future__ import annotations

import pytest

from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.policy import (
    Actor,
    ActorKind,
    PolicyDecision,
    PolicyEngine,
    Scope,
)


def _user() -> Actor:
    return Actor(kind=ActorKind.USER, id="01KS900000000000000000USR1")


def _agent() -> Actor:
    return Actor(kind=ActorKind.AGENT_SESSION, id="01KS900000000000000000AGT1")


@pytest.mark.asyncio
async def test_deploy_prod_denied_for_anyone() -> None:
    engine = PolicyEngine()
    for actor in (_user(), _agent()):
        ev = await engine.evaluate(action="deploy.prod", actor=actor)
        assert ev.decision is PolicyDecision.DENY


@pytest.mark.asyncio
async def test_deploy_dev_requires_user_approval() -> None:
    engine = PolicyEngine()
    ev = await engine.evaluate(action="deploy.dev", actor=_user())
    assert ev.decision is PolicyDecision.REQUIRE_USER_APPROVAL


@pytest.mark.asyncio
async def test_agent_cannot_mutate_secrets() -> None:
    engine = PolicyEngine()
    for action in ("secret.create", "secret.update", "secret.delete"):
        ev = await engine.evaluate(action=action, actor=_agent())
        assert ev.decision is PolicyDecision.DENY
        assert "user-only" in ev.reason


@pytest.mark.asyncio
async def test_secret_read_allowed() -> None:
    engine = PolicyEngine()
    for actor in (_user(), _agent()):
        ev = await engine.evaluate(action="secret.read", actor=actor)
        assert ev.decision is PolicyDecision.ALLOW


@pytest.mark.asyncio
async def test_force_push_denied_for_agent_and_user() -> None:
    engine = PolicyEngine()
    for actor in (_user(), _agent()):
        ev = await engine.evaluate(action="git.push.force", actor=actor)
        assert ev.decision is PolicyDecision.DENY


@pytest.mark.asyncio
async def test_unknown_action_allowed() -> None:
    engine = PolicyEngine()
    ev = await engine.evaluate(action="custom.tool.read_only", actor=_user())
    assert ev.decision is PolicyDecision.ALLOW


@pytest.mark.asyncio
async def test_audit_sink_records_evaluation() -> None:
    sink = InMemoryAuditSink()
    engine = PolicyEngine(audit_sink=sink)
    await engine.evaluate(
        action="deploy.prod",
        actor=_agent(),
        scope=Scope(project_id="01KS900000000000000000PRJX"),
    )
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev.action == "policy.evaluate"
    assert ev.outcome.value == "denied"
    assert ev.subject == {"action": "deploy.prod"}
    assert ev.payload["decision"] == "deny"


@pytest.mark.asyncio
async def test_evaluate_returns_carries_scope_and_context() -> None:
    engine = PolicyEngine()
    scope = Scope(project_id="p", workspace_id="w", environment_id="e")
    context = {"target": "prod"}
    ev = await engine.evaluate(
        action="deploy.staging",
        actor=_user(),
        scope=scope,
        context=context,
    )
    assert ev.scope == scope
    assert ev.context == context
    assert ev.action == "deploy.staging"
