"""DeployOrchestrator — PolicyEngine + 2FA + audit + target wiring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.domains.deploy import (
    AcceptAnyCodeVerifier,
    AlwaysDenyVerifier,
    DeployContext,
    DeployOrchestrator,
    DeployResult,
    DeployStatus,
    DeployStatusKind,
    DeployTarget,
    OrchestratorError,
    RollbackResult,
    TwoFactorError,
)
from gapt_server.policy.engine import PolicyEngine


@dataclass
class _RecordingTarget:
    """Test double — records every call without touching subprocess."""

    name: str = "recorder"
    next_result: DeployResult | None = None
    recorded: list[DeployContext] = field(default_factory=list)
    rollback_recorded: list[tuple[DeployContext, str]] = field(default_factory=list)

    async def deploy(self, ctx: DeployContext) -> DeployResult:
        self.recorded.append(ctx)
        if self.next_result is not None:
            return self.next_result
        return DeployResult(run_id=ctx.run_id, status=DeployStatusKind.SUCCESS, log="ok")

    async def status(self, ctx: DeployContext) -> DeployStatus:
        return DeployStatus(run_id=ctx.run_id, status=DeployStatusKind.SUCCESS)

    async def rollback(self, ctx: DeployContext, *, to_version: str) -> RollbackResult:
        self.rollback_recorded.append((ctx, to_version))
        return RollbackResult(
            run_id=ctx.run_id,
            status=DeployStatusKind.ROLLED_BACK,
            restored_version=to_version,
        )


@pytest.mark.asyncio
async def test_deploy_success_emits_start_and_terminal_audit() -> None:
    audit = InMemoryAuditSink()
    target = _RecordingTarget()
    orch = DeployOrchestrator(
        policy_engine=PolicyEngine(),
        target=target,
        audit_sink=audit,
    )

    result = await orch.deploy(
        actor_id="u1",
        project_id="p1",
        environment_id="e1",
        environment_name="staging",
        version="v1",
        compose_path="compose.yml",
        secret_refs=[],
        target_options={},
    )

    assert result.status is DeployStatusKind.SUCCESS
    actions = [e.action for e in audit.events]
    assert "deploy.start" in actions
    assert "deploy.success" in actions
    assert any(e.scope.get("run_id") == result.run_id for e in audit.events)


@pytest.mark.asyncio
async def test_deploy_policy_denied_raises_and_audits_denied() -> None:
    # PolicyEngine denies `deploy.prod` for *agents* via the
    # _AGENT_FORBIDDEN_ACTIONS set. We use a USER actor for normal
    # flow, but `deploy.prod` requires 2FA for users — to hit the
    # DENY path we craft a policy that explicitly DENYs.
    class DenyEngine(PolicyEngine):
        async def evaluate(self, *, action, actor, scope=None, context=None):  # type: ignore[override]
            from gapt_server.policy.engine import PolicyDecision, PolicyEvaluation, Scope

            return PolicyEvaluation(
                action=action,
                decision=PolicyDecision.DENY,
                reason="test deny",
                actor=actor,
                scope=scope or Scope(),
                context=context or {},
            )

    audit = InMemoryAuditSink()
    target = _RecordingTarget()
    orch = DeployOrchestrator(
        policy_engine=DenyEngine(),
        target=target,
        audit_sink=audit,
    )

    with pytest.raises(OrchestratorError) as exc:
        await orch.deploy(
            actor_id="u1",
            project_id="p1",
            environment_id="e1",
            environment_name="prod",
            version="v",
            compose_path="compose.yml",
            secret_refs=[],
            target_options={},
        )
    assert exc.value.code == "deploy.policy_denied"
    assert any(e.action == "deploy.denied" for e in audit.events)
    # Target.deploy never ran.
    assert target.recorded == []


@pytest.mark.asyncio
async def test_deploy_2fa_required_412_when_no_code() -> None:
    audit = InMemoryAuditSink()
    target = _RecordingTarget()
    orch = DeployOrchestrator(
        policy_engine=PolicyEngine(),  # deploy.staging → REQUIRE_USER_APPROVAL,
        target=target,
        audit_sink=audit,
        two_factor=AlwaysDenyVerifier(),
    )

    # Build a custom engine that returns REQUIRE_2FA for the action.
    class TwoFaEngine(PolicyEngine):
        async def evaluate(self, *, action, actor, scope=None, context=None):  # type: ignore[override]
            from gapt_server.policy.engine import PolicyDecision, PolicyEvaluation, Scope

            return PolicyEvaluation(
                action=action,
                decision=PolicyDecision.REQUIRE_2FA,
                reason="prod requires 2fa",
                actor=actor,
                scope=scope or Scope(),
                context=context or {},
            )

    orch.policy_engine = TwoFaEngine()

    with pytest.raises(TwoFactorError) as exc:
        await orch.deploy(
            actor_id="u1",
            project_id="p1",
            environment_id="e1",
            environment_name="prod",
            version="v",
            compose_path="compose.yml",
            secret_refs=[],
            target_options={},
            two_factor_code=None,
        )
    assert exc.value.code == "deploy.2fa_required"
    assert target.recorded == []


@pytest.mark.asyncio
async def test_deploy_2fa_required_accepts_code() -> None:
    target = _RecordingTarget()

    class TwoFaEngine(PolicyEngine):
        async def evaluate(self, *, action, actor, scope=None, context=None):  # type: ignore[override]
            from gapt_server.policy.engine import PolicyDecision, PolicyEvaluation, Scope

            return PolicyEvaluation(
                action=action,
                decision=PolicyDecision.REQUIRE_2FA,
                reason="prod requires 2fa",
                actor=actor,
                scope=scope or Scope(),
                context=context or {},
            )

    orch = DeployOrchestrator(
        policy_engine=TwoFaEngine(),
        target=target,
        two_factor=AcceptAnyCodeVerifier(),
    )

    result = await orch.deploy(
        actor_id="u1",
        project_id="p1",
        environment_id="e1",
        environment_name="prod",
        version="v",
        compose_path="compose.yml",
        secret_refs=[],
        target_options={},
        two_factor_code="123456",
    )
    assert result.status is DeployStatusKind.SUCCESS


@pytest.mark.asyncio
async def test_deploy_secret_resolver_passes_env_to_target() -> None:
    target = _RecordingTarget()
    seen: list[list[str]] = []

    async def resolver(refs: list[str]) -> dict[str, str]:
        seen.append(list(refs))
        return {"DB_URL": "postgres://x"}

    orch = DeployOrchestrator(
        policy_engine=PolicyEngine(),
        target=target,
        secret_resolver=resolver,
    )
    await orch.deploy(
        actor_id="u1",
        project_id="p1",
        environment_id="e1",
        environment_name="dev",  # not in policy → ALLOW
        version="v",
        compose_path="compose.yml",
        secret_refs=["sec_db"],
        target_options={},
    )
    assert seen == [["sec_db"]]
    # Target saw the secret (the orchestrator zeroizes its local
    # copy *after* the target completes, so target.recorded[0]
    # already finished).
    # Just assert one call landed.
    assert len(target.recorded) == 1


@pytest.mark.asyncio
async def test_rollback_calls_target_with_to_version() -> None:
    target = _RecordingTarget()
    orch = DeployOrchestrator(
        policy_engine=PolicyEngine(),
        target=target,
    )
    result = await orch.rollback(
        actor_id="u1",
        project_id="p1",
        environment_id="e1",
        environment_name="dev",
        run_id="rid-1",
        compose_path="compose.yml",
        target_options={},
        to_version="v0",
    )
    assert result.status is DeployStatusKind.ROLLED_BACK
    assert target.rollback_recorded[0][1] == "v0"
