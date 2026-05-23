"""PRE / POST tool-use mirror into the AuditSink.

Subject + scope shape matches Cycle 1.6's project audit events so
analytics queries can join everything by ``scope_jsonb->>'session_id'``.
Tool inputs are scrubbed through ``masking.scrub`` before insert via
the sink's own pipeline — we don't pre-redact here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from geny_executor.hooks import HookEventPayload, HookOutcome

from gapt_server.db import enums
from gapt_server.domains.audit.sink import AuditEvent, AuditSink

AuditHookHandler = Callable[[HookEventPayload], Awaitable[HookOutcome]]


@dataclass(frozen=True)
class _AuditScope:
    actor_id: str
    project_id: str
    workspace_id: str
    session_id: str


def _scope_dict(scope: _AuditScope) -> dict[str, object]:
    return {
        "project_id": scope.project_id,
        "workspace_id": scope.workspace_id,
        "session_id": scope.session_id,
    }


def build_audit_hook(
    *,
    sink: AuditSink,
    actor_id: str,
    project_id: str,
    workspace_id: str,
    session_id: str,
) -> tuple[AuditHookHandler, AuditHookHandler, AuditHookHandler]:
    """Return ``(pre, post_ok, post_fail)`` handlers ready to register
    against ``PRE_TOOL_USE`` / ``POST_TOOL_USE`` / ``POST_TOOL_FAILURE``.
    """
    scope = _AuditScope(
        actor_id=actor_id,
        project_id=project_id,
        workspace_id=workspace_id,
        session_id=session_id,
    )

    async def pre(payload: HookEventPayload) -> HookOutcome:
        await sink.log(
            AuditEvent(
                action="agent.tool_invoke",
                actor_type=enums.AuditActorType.AGENT_SESSION,
                actor_id=actor_id,
                outcome=enums.AuditOutcome.OK,
                scope=_scope_dict(scope),
                subject={"tool_name": payload.tool_name},
                payload={"tool_input": payload.tool_input or {}},
            )
        )
        return HookOutcome.passthrough()

    async def post_ok(payload: HookEventPayload) -> HookOutcome:
        await sink.log(
            AuditEvent(
                action="agent.tool_complete",
                actor_type=enums.AuditActorType.AGENT_SESSION,
                actor_id=actor_id,
                outcome=enums.AuditOutcome.OK,
                scope=_scope_dict(scope),
                subject={"tool_name": payload.tool_name},
                duration_ms=(payload.details or {}).get("duration_ms"),
            )
        )
        return HookOutcome.passthrough()

    async def post_fail(payload: HookEventPayload) -> HookOutcome:
        details = payload.details or {}
        await sink.log(
            AuditEvent(
                action="agent.tool_failure",
                actor_type=enums.AuditActorType.AGENT_SESSION,
                actor_id=actor_id,
                outcome=enums.AuditOutcome.ERROR,
                scope=_scope_dict(scope),
                subject={"tool_name": payload.tool_name},
                exec_code=details.get("code"),
                duration_ms=details.get("duration_ms"),
                payload={"error": details.get("error", "")[:400]},
            )
        )
        return HookOutcome.passthrough()

    return pre, post_ok, post_fail
