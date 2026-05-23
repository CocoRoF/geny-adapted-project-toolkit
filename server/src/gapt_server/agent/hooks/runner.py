"""``build_hook_runner`` — wires the three hooks onto a geny-executor
``HookRunner`` ready to ``pipeline.attach_runtime(hook_runner=…)``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from geny_executor.hooks import HookConfig, HookEvent, HookRunner

from gapt_server.agent.hooks.audit_hook import build_audit_hook
from gapt_server.agent.hooks.cost_hook import (
    CostAccumulator,
    CostCallback,
    build_cost_hook,
)
from gapt_server.agent.hooks.policy_hook import PolicyHookConfig, build_policy_hook

if TYPE_CHECKING:
    from gapt_server.domains.audit.sink import AuditSink
    from gapt_server.policy.engine import PolicyEngine


def build_hook_runner(
    *,
    engine: PolicyEngine,
    audit_sink: AuditSink,
    actor_id: str,
    project_id: str,
    workspace_id: str,
    session_id: str,
    on_cost_update: CostCallback | None = None,
) -> tuple[HookRunner, CostAccumulator]:
    """Return ``(runner, cost_accumulator)``. The runner already has
    policy + audit + cost hooks registered.

    The accumulator is returned so the SSE layer can read snapshots
    independently of the (otherwise fire-and-forget) on_update
    callback. Cycle 2.10 reads `accumulator.snapshot()` on a 1-second
    debounced timer.
    """
    # HookRunner.enabled = config.enabled AND env opt-in. The env
    # switch was added to gate *subprocess* hooks; we register only
    # in-process handlers here (policy / audit / cost) — but the gate
    # still short-circuits them. Synthesise a hook-enabled env so the
    # in-process chain runs whether or not the operator opted in for
    # subprocess hooks (which we don't register anyway).
    runner_env = dict(os.environ)
    runner_env["GENY_ALLOW_HOOKS"] = "1"
    runner = HookRunner(HookConfig(enabled=True), env=runner_env)

    policy_handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(
            actor_id=actor_id, project_id=project_id, workspace_id=workspace_id
        ),
    )
    pre_audit, post_audit_ok, post_audit_fail = build_audit_hook(
        sink=audit_sink,
        actor_id=actor_id,
        project_id=project_id,
        workspace_id=workspace_id,
        session_id=session_id,
    )

    accumulator = CostAccumulator(session_id=session_id)
    cost_handler = build_cost_hook(accumulator=accumulator, on_update=on_cost_update)

    runner.register_in_process(HookEvent.PRE_TOOL_USE, policy_handler)
    runner.register_in_process(HookEvent.PRE_TOOL_USE, pre_audit)
    runner.register_in_process(HookEvent.POST_TOOL_USE, post_audit_ok)
    runner.register_in_process(HookEvent.POST_TOOL_USE, cost_handler)
    runner.register_in_process(HookEvent.POST_TOOL_FAILURE, post_audit_fail)
    return runner, accumulator
