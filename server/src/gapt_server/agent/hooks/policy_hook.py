"""``PRE_TOOL_USE`` hook backed by the PolicyEngine (Cycle 1.10).

Tool name → policy action mapping is intentionally narrow. The handful
of agent-callable actions we ship today are exactly the ones gapt_git
/ gapt_pr surface. Everything else falls through to a generic
``tool.<name>`` action — the engine's default-allow keeps unknowns
moving (per [09](docs/09_security_authz_observability.md) §9.2.3).

This is *Layer 1* per M0-P3 PR4's `decision_two_layer_policy.md` —
fires for SDK providers that route tools through Stage 10. For the
``claude_code_cli`` path, the MCP bridge's daemon-side gate (Layer
2b, Cycle 2.4) is what actually enforces. We register both so the
deployment never depends on which provider the user picked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from geny_executor.hooks import HookEventPayload, HookOutcome

from gapt_server.policy.engine import Actor, ActorKind, PolicyDecision, Scope

if TYPE_CHECKING:
    from gapt_server.policy.engine import PolicyEngine

logger = structlog.get_logger(__name__)


PolicyHook = Callable[[HookEventPayload], Awaitable[HookOutcome]]


@dataclass(frozen=True)
class PolicyHookConfig:
    """How the PolicyEngine sees a tool invocation.

    `actor_id` ties the audit + decision to the right user; the
    SessionManager fills this in before attaching the hook runner to
    the pipeline.
    """

    actor_id: str
    project_id: str
    workspace_id: str


# Phase D.1 — tools that mutate workspace state. In Plan mode the
# policy hook short-circuits to block before the engine even runs.
# Keep this list narrow and explicit: missing a tool here would let
# Plan mode silently fall back to allow, which would defeat the gate.
PLAN_MUTATION_TOOLS: frozenset[str] = frozenset(
    {"gapt_edit", "gapt_git", "gapt_pr"}
)


@dataclass
class ChatModeRef:
    """Mutable per-session container for the Plan/Act mode flag.

    The runtime updates ``mode`` on every `invoke()`; the policy
    hook reads it at PRE_TOOL_USE time. A shared reference (not a
    copy) so the hook always sees the latest value without the
    runtime needing to rebuild the hook chain on each mode switch.
    """

    mode: str = "act"


_TOOL_TO_ACTION: dict[str, str] = {
    # gapt_git push branches into protected vs feature paths; the
    # daemon-side gate already refuses protected, so the engine here
    # gets `tool.gapt_git.push` and applies any project override.
    "gapt_git": "tool.gapt_git",
    "gapt_pr": "tool.gapt_pr",
    "gapt_edit": "tool.gapt_edit",
    "gapt_read": "tool.gapt_read",
    "gapt_glob": "tool.gapt_glob",
    "gapt_grep": "tool.gapt_grep",
}


def _resolve_action(tool_name: str | None) -> str:
    if tool_name is None:
        return "tool.unknown"
    return _TOOL_TO_ACTION.get(tool_name, f"tool.{tool_name}")


def build_policy_hook(
    *,
    engine: PolicyEngine,
    config: PolicyHookConfig,
    mode_ref: ChatModeRef | None = None,
) -> PolicyHook:
    """Return an async handler suitable for
    ``HookRunner.register_in_process(HookEvent.PRE_TOOL_USE, handler)``.

    Decisions:
    - ``ALLOW``    → ``HookOutcome.passthrough()``
    - ``DENY``     → ``HookOutcome.block(reason)`` — stage 10 sees this
                     as a deny and raises ``ToolError(access_denied)``.
    - ``REQUIRE_*`` → block with a reason saying "user confirmation
                     required". Cycle 2.10's SSE layer surfaces the
                     reason text to the UI prompt; M1 ships block-only
                     handling — auto-approval flow is M1-E4.

    When ``mode_ref`` is provided and reports ``mode == "plan"``,
    every mutating tool (see :data:`PLAN_MUTATION_TOOLS`) is rejected
    *before* the engine runs. Read-only tools fall through to the
    normal engine path in either mode. Phase D.1.
    """

    actor = Actor(kind=ActorKind.AGENT_SESSION, id=config.actor_id)
    scope = Scope(project_id=config.project_id, workspace_id=config.workspace_id)

    async def handler(payload: HookEventPayload) -> HookOutcome:
        if (
            mode_ref is not None
            and mode_ref.mode == "plan"
            and payload.tool_name in PLAN_MUTATION_TOOLS
        ):
            logger.info(
                "policy_hook.plan_mode_block",
                tool_name=payload.tool_name,
                actor_id=config.actor_id,
            )
            return HookOutcome.block(
                f"Plan mode blocks mutating tool {payload.tool_name!r}. "
                "Switch to Act mode to apply changes."
            )
        action = _resolve_action(payload.tool_name)
        evaluation = await engine.evaluate(
            action=action,
            actor=actor,
            scope=scope,
            context={"tool_name": payload.tool_name, "tool_input": payload.tool_input or {}},
        )
        if evaluation.decision is PolicyDecision.ALLOW:
            return HookOutcome.passthrough()
        if evaluation.decision is PolicyDecision.DENY:
            logger.info(
                "policy_hook.deny",
                action=action,
                tool_name=payload.tool_name,
                actor_id=config.actor_id,
                reason=evaluation.reason,
            )
            return HookOutcome.block(f"PolicyEngine denied {action!r}: {evaluation.reason}")
        # REQUIRE_USER_APPROVAL / REQUIRE_2FA both block until the
        # interactive flow runs. M1 ships pre-blocking only.
        logger.info(
            "policy_hook.require_user",
            action=action,
            tool_name=payload.tool_name,
            decision=evaluation.decision.value,
        )
        return HookOutcome.block(
            f"{action!r} requires explicit user confirmation "
            f"({evaluation.decision.value}): {evaluation.reason}"
        )

    return handler
