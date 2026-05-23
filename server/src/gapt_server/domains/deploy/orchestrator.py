"""Build/Deploy Orchestrator (D6).

Wraps a `DeployTarget` with the cross-cutting concerns documented in
plan §4.2:

  1. PolicyEngine evaluates `deploy.{environment}`.
  2. REQUIRE_2FA → TwoFactorVerifier (412 on missing / invalid code).
  3. Secret Vault read → plaintext bundle (single-use).
  4. DeployTarget.deploy(ctx).
  5. Audit on entry + exit (action `deploy.run`, scope contains
     project_id / environment_id / run_id).

The Orchestrator is *stateless across runs*. Each call gets a fresh
`run_id`; per-run state lives inside the target's `_runs` map.

Concurrent in-progress deploys to the *same environment* are
prevented at the API layer (router checks `DeployRun` rows in the
audit log) — the orchestrator itself doesn't lock; deferring to the
caller keeps the unit testable.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

import structlog

from gapt_server.db import enums
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import AuditEvent, AuditSink, NullAuditSink
from gapt_server.domains.deploy.protocol import (
    DeployContext,
    DeployRequest,
    DeployResult,
    DeployStatusKind,
    DeployTarget,
    RollbackResult,
)
from gapt_server.domains.deploy.two_factor import (
    AcceptAnyCodeVerifier,
    TwoFactorError,
    TwoFactorVerifier,
)
from gapt_server.policy.engine import Actor, ActorKind, PolicyDecision, PolicyEngine, Scope

logger = structlog.get_logger(__name__)


SecretBundleResolver = Callable[[list[str]], Awaitable[dict[str, str]]]


async def _empty_resolver(_refs: list[str]) -> dict[str, str]:
    """Default secret resolver — returns no secrets. Real wiring
    happens in the router once SecretVault is in scope."""
    return {}


class OrchestratorError(RuntimeError):
    """Carries a stable exec.*.* code + HTTP-mapping hint."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class DeployOrchestrator:
    """Stitches PolicyEngine + 2FA + Secret Vault + DeployTarget."""

    policy_engine: PolicyEngine
    target: DeployTarget
    audit_sink: AuditSink = field(default_factory=NullAuditSink)
    two_factor: TwoFactorVerifier = field(default_factory=AcceptAnyCodeVerifier)
    # Hook the router fills in to fetch plaintext secrets from Vault.
    secret_resolver: SecretBundleResolver = field(default=_empty_resolver)

    async def deploy(
        self,
        *,
        actor_id: str,
        project_id: str,
        environment_id: str,
        environment_name: str,
        version: str,
        compose_path: str,
        secret_refs: list[str],
        target_options: dict[str, object],
        compose_paths: list[str] | None = None,
        two_factor_code: str | None = None,
    ) -> DeployResult:
        run_id = new_ulid()
        action = f"deploy.{environment_name}"
        scope = Scope(project_id=project_id, environment_id=environment_id)

        # 1) PolicyEngine
        evaluation = await self.policy_engine.evaluate(
            action=action,
            actor=Actor(kind=ActorKind.USER, id=actor_id),
            scope=scope,
            context={"version": version, "run_id": run_id},
        )
        if evaluation.decision is PolicyDecision.DENY:
            await self._audit(
                action="deploy.denied",
                actor_id=actor_id,
                outcome=enums.AuditOutcome.DENIED,
                scope=scope,
                run_id=run_id,
                payload={"reason": evaluation.reason},
                exec_code="deploy.policy_denied",
            )
            raise OrchestratorError("deploy.policy_denied", evaluation.reason)

        # 2) 2FA gate
        if evaluation.decision is PolicyDecision.REQUIRE_2FA:
            ok = await self.two_factor.verify(user_id=actor_id, code=two_factor_code)
            if not ok:
                await self._audit(
                    action="deploy.denied",
                    actor_id=actor_id,
                    outcome=enums.AuditOutcome.DENIED,
                    scope=scope,
                    run_id=run_id,
                    payload={"reason": "2FA required"},
                    exec_code="deploy.2fa_required",
                )
                raise TwoFactorError(
                    "deploy.2fa_required",
                    f"action {action!r} requires 2FA — provide a valid code",
                )

        # REQUIRE_USER_APPROVAL — caller already clicked Approve to
        # reach this code path. We still log the click for audit
        # symmetry.
        if evaluation.decision is PolicyDecision.REQUIRE_USER_APPROVAL:
            await self._audit(
                action="deploy.user_approved",
                actor_id=actor_id,
                outcome=enums.AuditOutcome.OK,
                scope=scope,
                run_id=run_id,
            )

        # 3) Secrets
        env_secrets: dict[str, str] = {}
        if secret_refs:
            try:
                env_secrets = await self.secret_resolver(secret_refs)
            except Exception as exc:
                await self._audit(
                    action="deploy.secret_fetch_failed",
                    actor_id=actor_id,
                    outcome=enums.AuditOutcome.ERROR,
                    scope=scope,
                    run_id=run_id,
                    exec_code="deploy.secret_fetch_failed",
                    payload={"error": str(exc)[:400]},
                )
                raise OrchestratorError(
                    "deploy.secret_fetch_failed",
                    f"could not read secrets from vault: {exc}",
                ) from exc

        # 4) Hand off to the target.
        await self._audit(
            action="deploy.start",
            actor_id=actor_id,
            outcome=enums.AuditOutcome.OK,
            scope=scope,
            run_id=run_id,
            payload={"version": version, "environment": environment_name},
        )

        request = DeployRequest(
            project_id=project_id,
            environment=environment_name,
            version=version,
            compose_path=compose_path,
            compose_paths=compose_paths or [],
            env_secrets=env_secrets,
            target_options=target_options,
        )
        ctx = DeployContext(run_id=run_id, request=request)

        try:
            result = await self.target.deploy(ctx)
        finally:
            # Zeroize so the orchestrator's reference doesn't keep
            # the plaintext alive past the target's own cleanup.
            for k in list(env_secrets.keys()):
                env_secrets[k] = ""

        # 5) Terminal audit.
        await self._audit(
            action=f"deploy.{result.status.value}",
            actor_id=actor_id,
            outcome=(
                enums.AuditOutcome.OK
                if result.status is DeployStatusKind.SUCCESS
                else enums.AuditOutcome.ERROR
            ),
            scope=scope,
            run_id=run_id,
            exec_code=result.exec_code,
            payload={"log_tail": result.log[-1500:] if result.log else ""},
        )
        return result

    async def rollback(
        self,
        *,
        actor_id: str,
        project_id: str,
        environment_id: str,
        environment_name: str,
        run_id: str,
        compose_path: str,
        target_options: dict[str, object],
        to_version: str,
        compose_paths: list[str] | None = None,
        two_factor_code: str | None = None,
    ) -> RollbackResult:
        """Roll back a prior deploy run by re-applying `to_version`."""
        action = f"deploy.{environment_name}"
        scope = Scope(project_id=project_id, environment_id=environment_id)

        evaluation = await self.policy_engine.evaluate(
            action=action,
            actor=Actor(kind=ActorKind.USER, id=actor_id),
            scope=scope,
        )
        if evaluation.decision is PolicyDecision.DENY:
            raise OrchestratorError("deploy.policy_denied", evaluation.reason)
        if evaluation.decision is PolicyDecision.REQUIRE_2FA:
            ok = await self.two_factor.verify(user_id=actor_id, code=two_factor_code)
            if not ok:
                raise TwoFactorError(
                    "deploy.2fa_required",
                    "rollback requires 2FA — provide a valid code",
                )

        request = DeployRequest(
            project_id=project_id,
            environment=environment_name,
            version=to_version,
            compose_path=compose_path,
            compose_paths=compose_paths or [],
            target_options=target_options,
        )
        ctx = DeployContext(run_id=run_id, request=request)
        result = await self.target.rollback(ctx, to_version=to_version)

        await self._audit(
            action="deploy.rollback",
            actor_id=actor_id,
            outcome=(
                enums.AuditOutcome.OK
                if result.status is DeployStatusKind.ROLLED_BACK
                else enums.AuditOutcome.ERROR
            ),
            scope=scope,
            run_id=run_id,
            exec_code=result.exec_code,
            payload={
                "restored_version": result.restored_version,
                "log_tail": result.log[-1500:] if result.log else "",
            },
        )
        return result

    async def stream_status(
        self,
        *,
        run_id: str,
        project_id: str,
        environment_name: str,
        compose_path: str,
        target_options: dict[str, object],
        poll_interval_s: float = 0.5,
        max_polls: int = 240,  # ~2 min default
    ) -> AsyncIterator[str]:
        """Yield JSON-ish status frames suitable for SSE.

        Polls `target.status()` until the run finishes or
        `max_polls` is reached. The orchestrator doesn't know the
        DeployRequest details at status-poll time, so the router
        passes the same `compose_path` / `target_options` (they live
        in the Environment row)."""
        request = DeployRequest(
            project_id=project_id,
            environment=environment_name,
            version="<status-poll>",
            compose_path=compose_path,
            target_options=target_options,
        )
        ctx = DeployContext(run_id=run_id, request=request)
        for _ in range(max_polls):
            status = await self.target.status(ctx)
            code_part = (
                "null"
                if status.exec_code is None
                else f'"{status.exec_code}"'
            )
            yield (
                f'{{"run_id":"{run_id}","status":"{status.status.value}",'
                f'"exec_code":{code_part}}}'
            )
            if status.status in {
                DeployStatusKind.SUCCESS,
                DeployStatusKind.FAILED,
                DeployStatusKind.ROLLED_BACK,
            }:
                return
            await asyncio.sleep(poll_interval_s)

    async def _audit(
        self,
        *,
        action: str,
        actor_id: str,
        outcome: enums.AuditOutcome,
        scope: Scope,
        run_id: str,
        payload: dict[str, object] | None = None,
        exec_code: str | None = None,
    ) -> None:
        await self.audit_sink.log(
            AuditEvent(
                action=action,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor_id,
                outcome=outcome,
                scope={
                    "project_id": scope.project_id,
                    "environment_id": scope.environment_id,
                    "run_id": run_id,
                },
                subject={},
                payload=payload or {},
                exec_code=exec_code,
            )
        )
