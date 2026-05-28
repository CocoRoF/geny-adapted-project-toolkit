"""HookRunner — policy / audit / cost wired together."""

from __future__ import annotations

from typing import Any

import pytest
from geny_executor.hooks import HookEvent, HookEventPayload

from gapt_server.agent.hooks import (
    ChatModeRef,
    CostAccumulator,
    PolicyHookConfig,
    build_audit_hook,
    build_cost_hook,
    build_hook_runner,
    build_policy_hook,
)
from gapt_server.agent.hooks import policy_hook as pol_mod
from gapt_server.domains.audit.sink import InMemoryAuditSink
from gapt_server.policy.engine import PolicyEngine


def _payload(
    *,
    tool_name: str = "gapt_read",
    tool_input: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    event: HookEvent = HookEvent.PRE_TOOL_USE,
) -> HookEventPayload:
    return HookEventPayload(
        event=event,
        session_id="01KS900000000000000000SESS",
        timestamp="2026-05-23T00:00:00Z",
        pipeline_id="pid",
        permission_mode="default",
        stage_order=10,
        stage_name="tool",
        tool_name=tool_name,
        tool_input=tool_input or {},
        details=details if details is not None else {},
    )


# ─────────────────────────────────────────────── policy hook ──


@pytest.mark.asyncio
async def test_policy_hook_allows_read_only_tools() -> None:
    engine = PolicyEngine()
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
    )
    outcome = await handler(_payload(tool_name="gapt_read"))
    assert outcome.continue_ is True


# ───────────────────────────────────────── Phase D.1 plan mode ──


@pytest.mark.asyncio
async def test_plan_mode_blocks_gapt_edit() -> None:
    """Plan mode short-circuits every mutating tool BEFORE the engine
    runs. The hook returns a block whose reason names the mode so the
    UI can render a targeted "switch to Act" prompt."""
    engine = PolicyEngine()
    mode_ref = ChatModeRef(mode="plan")
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
        mode_ref=mode_ref,
    )
    outcome = await handler(_payload(tool_name="gapt_edit"))
    assert outcome.continue_ is False
    assert "Plan mode" in (outcome.stop_reason or "")
    assert "gapt_edit" in (outcome.stop_reason or "")


@pytest.mark.asyncio
async def test_plan_mode_blocks_gapt_git_and_gapt_pr() -> None:
    engine = PolicyEngine()
    mode_ref = ChatModeRef(mode="plan")
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
        mode_ref=mode_ref,
    )
    for tool in ("gapt_git", "gapt_pr"):
        outcome = await handler(_payload(tool_name=tool))
        assert outcome.continue_ is False, tool
        assert "Plan mode" in (outcome.stop_reason or ""), tool


@pytest.mark.asyncio
async def test_plan_mode_allows_read_only_tools() -> None:
    """The whole point of Plan mode — reads, greps, globs stay open."""
    engine = PolicyEngine()
    mode_ref = ChatModeRef(mode="plan")
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
        mode_ref=mode_ref,
    )
    for tool in ("gapt_read", "gapt_glob", "gapt_grep"):
        outcome = await handler(_payload(tool_name=tool))
        assert outcome.continue_ is True, tool


@pytest.mark.asyncio
async def test_act_mode_allows_mutating_tools() -> None:
    """When mode is "act" the plan gate is a no-op — the engine
    decision (default-allow for unknown actions) wins."""
    engine = PolicyEngine()
    mode_ref = ChatModeRef(mode="act")
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
        mode_ref=mode_ref,
    )
    for tool in ("gapt_edit", "gapt_git", "gapt_pr"):
        outcome = await handler(_payload(tool_name=tool))
        assert outcome.continue_ is True, tool


@pytest.mark.asyncio
async def test_mode_flip_after_hook_built_is_observed() -> None:
    """The `mode_ref` is mutable + shared. Flipping it after the hook
    is built must affect the next tool call — without this, the
    SessionRuntime couldn't toggle Plan/Act mid-conversation."""
    engine = PolicyEngine()
    mode_ref = ChatModeRef(mode="act")
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
        mode_ref=mode_ref,
    )
    # Initial: act mode allows the mutating tool.
    assert (await handler(_payload(tool_name="gapt_edit"))).continue_ is True
    # Flip to plan — next call blocks.
    mode_ref.mode = "plan"
    assert (await handler(_payload(tool_name="gapt_edit"))).continue_ is False
    # Flip back — allows again.
    mode_ref.mode = "act"
    assert (await handler(_payload(tool_name="gapt_edit"))).continue_ is True


@pytest.mark.asyncio
async def test_plan_mode_without_mode_ref_is_legacy_behavior() -> None:
    """If no mode_ref is passed, the hook behaves exactly as before
    Phase D.1 — no plan gate, just the engine decision."""
    engine = PolicyEngine()
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
        # mode_ref intentionally omitted
    )
    outcome = await handler(_payload(tool_name="gapt_edit"))
    assert outcome.continue_ is True


@pytest.mark.asyncio
async def test_policy_hook_blocks_secret_create() -> None:
    """The default bundle denies `secret.create` even on a tool that
    doesn't exist — fall-through `tool.secret.create` lands in
    `_DEFAULTS` once mapped. We simulate the mapped case directly."""
    engine = PolicyEngine()
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
    )
    # Trick: rename payload's tool to one whose mapped action is
    # explicitly denied. We override the mapping at runtime to verify
    # the deny path actually flows through.
    pol_mod._TOOL_TO_ACTION["gapt_secret_create"] = "secret.create"
    try:
        outcome = await handler(_payload(tool_name="gapt_secret_create"))
    finally:
        pol_mod._TOOL_TO_ACTION.pop("gapt_secret_create", None)
    assert outcome.continue_ is False
    assert "PolicyEngine denied" in (outcome.stop_reason or "")


@pytest.mark.asyncio
async def test_policy_hook_blocks_require_user_approval() -> None:
    engine = PolicyEngine()
    handler = build_policy_hook(
        engine=engine,
        config=PolicyHookConfig(actor_id="u", project_id="p", workspace_id="w"),
    )
    # `git.push.protected` is REQUIRE_USER_APPROVAL in the default
    # bundle *and* not in `_AGENT_FORBIDDEN_ACTIONS`, so an agent
    # session lands on the REQUIRE_* branch instead of a hard deny.
    pol_mod._TOOL_TO_ACTION["gapt_git_protected"] = "git.push.protected"
    try:
        outcome = await handler(_payload(tool_name="gapt_git_protected"))
    finally:
        pol_mod._TOOL_TO_ACTION.pop("gapt_git_protected", None)
    assert outcome.continue_ is False
    assert "requires explicit user confirmation" in (outcome.stop_reason or "")


# ──────────────────────────────────────────────── audit hook ──


@pytest.mark.asyncio
async def test_audit_hook_emits_pre_post_failure() -> None:
    sink = InMemoryAuditSink()
    pre, post_ok, post_fail = build_audit_hook(
        sink=sink,
        actor_id="01KS900000000000000000USER",
        project_id="01KS900000000000000000PROJ",
        workspace_id="01KS90000000000000000WSP1",
        session_id="01KS900000000000000000SESS",
    )

    await pre(_payload(tool_name="gapt_read", tool_input={"path": "x"}))
    await post_ok(
        _payload(tool_name="gapt_read", details={"duration_ms": 12}, event=HookEvent.POST_TOOL_USE)
    )
    await post_fail(
        _payload(
            tool_name="gapt_edit",
            details={"code": "exec.tool.access_denied", "duration_ms": 5, "error": "no"},
            event=HookEvent.POST_TOOL_FAILURE,
        )
    )

    actions = [e.action for e in sink.events]
    assert actions == ["agent.tool_invoke", "agent.tool_complete", "agent.tool_failure"]
    failure = sink.events[2]
    assert failure.exec_code == "exec.tool.access_denied"
    assert failure.duration_ms == 5


# ────────────────────────────────────────────────── cost hook ──


@pytest.mark.asyncio
async def test_cost_hook_accumulates_tokens_and_calls_callback() -> None:
    acc = CostAccumulator(session_id="s")
    snapshots: list[dict[str, Any]] = []

    async def on_update(a: CostAccumulator) -> None:
        snapshots.append(a.snapshot())

    handler = build_cost_hook(accumulator=acc, on_update=on_update)
    await handler(
        _payload(
            tool_name="gapt_read",
            details={
                "duration_ms": 10,
                "input_tokens": 50,
                "output_tokens": 30,
                "cost_usd": 0.001,
            },
            event=HookEvent.POST_TOOL_USE,
        )
    )
    await handler(
        _payload(
            tool_name="gapt_read",
            details={"duration_ms": 20, "cost_usd": 0.002},
            event=HookEvent.POST_TOOL_USE,
        )
    )
    await handler(
        _payload(
            tool_name="gapt_edit",
            details={"input_tokens": 100, "output_tokens": 5},
            event=HookEvent.POST_TOOL_USE,
        )
    )

    assert acc.tool_calls == 3
    assert acc.input_tokens == 150
    assert acc.output_tokens == 35
    assert acc.cost_usd == pytest.approx(0.003)
    assert acc.tool_duration_ms == 30
    assert acc.by_tool == {"gapt_read": 2, "gapt_edit": 1}
    assert len(snapshots) == 3


@pytest.mark.asyncio
async def test_cost_hook_no_callback_still_works() -> None:
    acc = CostAccumulator(session_id="s")
    handler = build_cost_hook(accumulator=acc)
    await handler(
        _payload(
            tool_name="gapt_read",
            details={"input_tokens": 1},
            event=HookEvent.POST_TOOL_USE,
        )
    )
    assert acc.input_tokens == 1


# ──────────────────────────────────── full runner wiring ──


@pytest.mark.asyncio
async def test_build_hook_runner_registers_all_events() -> None:
    sink = InMemoryAuditSink()
    engine = PolicyEngine()
    runner, accumulator = build_hook_runner(
        engine=engine,
        audit_sink=sink,
        actor_id="u",
        project_id="p",
        workspace_id="w",
        session_id="s",
    )
    handlers = runner.list_in_process_handlers()
    # 2 for PRE_TOOL_USE (policy + audit), 2 for POST_TOOL_USE
    # (audit_ok + cost), 1 for POST_TOOL_FAILURE.
    assert handlers[HookEvent.PRE_TOOL_USE] == 2
    assert handlers[HookEvent.POST_TOOL_USE] == 2
    assert handlers[HookEvent.POST_TOOL_FAILURE] == 1
    assert accumulator.session_id == "s"


@pytest.mark.asyncio
async def test_runner_fires_chain_when_allowed() -> None:
    sink = InMemoryAuditSink()
    engine = PolicyEngine()
    runner, accumulator = build_hook_runner(
        engine=engine,
        audit_sink=sink,
        actor_id="u",
        project_id="p",
        workspace_id="w",
        session_id="s",
    )
    outcome = await runner.fire(HookEvent.PRE_TOOL_USE, _payload(tool_name="gapt_read"))
    # Policy allowed; audit pre fired. POST hasn't run yet.
    assert outcome.continue_ is True
    actions = [e.action for e in sink.events]
    assert actions == ["agent.tool_invoke"]

    post_payload = _payload(
        tool_name="gapt_read",
        details={"duration_ms": 7, "input_tokens": 3, "output_tokens": 2},
        event=HookEvent.POST_TOOL_USE,
    )
    await runner.fire(HookEvent.POST_TOOL_USE, post_payload)
    assert accumulator.tool_calls == 1
    assert accumulator.input_tokens == 3
