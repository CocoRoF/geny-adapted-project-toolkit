"""Server-side ``HookRunner`` wire-up.

Three hooks ship in M1-E2 Cycle 2.9:

- ``policy_hook`` — Layer-1 ``PRE_TOOL_USE`` gate. Calls the
  ``PolicyEngine`` from Cycle 1.10 and blocks the tool dispatch when
  the decision is DENY. Note: this is the *SDK-provider* layer.
  The ``claude_code_cli`` path uses the daemon-side (Layer 2b) gate
  shipped in Cycle 2.4 / 2.7; see
  ``poc/executor_agent/decision_two_layer_policy.md``.

- ``audit_hook`` — Mirrors every PRE/POST tool call into the
  ``AuditSink`` so the timeline matches the M1-E1 audit_events table.

- ``cost_hook`` — POST_TOOL_USE accumulator that streams duration_ms
  + token usage diffs into a supplied callback. Cycle 2.10's SSE
  layer turns the callback into a debounced ``event: cost`` push.

`build_hook_runner()` returns a ready-to-attach ``HookRunner`` with
all three registered. The function stays a free function rather than a
class because the runner itself is the stateful object.
"""

from gapt_server.agent.hooks.audit_hook import build_audit_hook
from gapt_server.agent.hooks.cost_hook import CostAccumulator, build_cost_hook
from gapt_server.agent.hooks.policy_hook import (
    ChatModeRef,
    PolicyHookConfig,
    build_policy_hook,
)
from gapt_server.agent.hooks.runner import build_hook_runner

__all__ = [
    "ChatModeRef",
    "CostAccumulator",
    "PolicyHookConfig",
    "build_audit_hook",
    "build_cost_hook",
    "build_hook_runner",
    "build_policy_hook",
]
