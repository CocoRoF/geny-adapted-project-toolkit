"""In-process registry of live agent sessions.

M1 keeps everything in one server process. Each `SessionRuntime` owns
the `Pipeline` instance returned by `ProjectAwareSessionManager`, the
SSE event bus, the hook accumulator, and (when invoke is running) the
asyncio.Task that is mapping pipeline events into SSE events.

M2 swaps this for Redis pub/sub so multiple workers can serve the same
session. The shape of `SessionRuntime` stays — only `SessionRegistry`
gains a sharded lookup.

`invoke()` is the seam: it kicks off the work in a background task and
returns immediately. The Task pulls `Pipeline.run_stream(message)` and
publishes onto the bus. For tests, callers can override the runner via
`invoke_runner=`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from geny_executor import EventTypes

from gapt_server.agent.streaming import (
    SessionEvent,
    SessionEventBus,
    SessionEventKind,
)

if TYPE_CHECKING:
    from geny_executor import ModelOverrides, Pipeline
    from geny_executor.core.state import PipelineState

    from gapt_server.agent.hooks.cost_hook import CostAccumulator
    from gapt_server.agent.hooks.policy_hook import ChatModeRef
    from gapt_server.domains.workspace_sandbox import WorkspaceSandbox


logger = structlog.get_logger(__name__)


# Pluggable so tests can mock pipeline.run_stream without spawning a
# real LLM call. The default below maps PipelineEvent → SessionEvent.
SessionInvokeRunner = Callable[
    ["SessionRuntime", str],
    Awaitable[None],
]


class SessionAlreadyInvoking(RuntimeError):
    """Raised when invoke is called while a prior task is still running."""


class SessionNotFound(KeyError):
    """Raised when the registry has no entry for a session id."""


@dataclass
class SessionRuntime:
    session_id: str
    project_id: str
    workspace_id: str
    user_id: str
    pipeline: Pipeline
    accumulator: CostAccumulator
    bus: SessionEventBus = field(default_factory=SessionEventBus)
    # The workspace's docker sandbox — bound by the router when the
    # runtime is built. None for tests / paths without a worktree.
    # Since geny-executor 2.2.0 the actual CLI routing lives on the
    # pipeline's attached `ClaudeCodeCLIClient(runner_factory=...)`
    # (see `agent/sandbox_runner.py`); this reference is kept for
    # observability / future per-session container ops.
    sandbox: WorkspaceSandbox | None = None
    # Phase D.1 — per-session Plan/Act mode reference shared with
    # the policy hook. `invoke(mode=...)` mutates it before kicking
    # off the work; the hook reads it on every PRE_TOOL_USE. None
    # for legacy paths that don't have a hook chain wired.
    mode_ref: ChatModeRef | None = None
    # Phase I.1 — invoked from `_drive_pipeline`'s `token.tracked`
    # branch so the DB cost columns + COST event publish + metrics
    # update fire even when the session has no tool calls. The
    # router sets this to the same `_on_cost_update` closure the
    # POST_TOOL_USE hook also calls; both paths are idempotent
    # (delta-detection inside the closure).
    cost_callback: Callable[[CostAccumulator], Awaitable[None]] | None = None
    # Phase I.3 — model string from the manifest's api stage config.
    # Used as the fallback-pricing key when the executor emits a
    # `token.tracked` payload with `cost_usd=0` (model-alias gap).
    # `None` keeps the pre-fix behaviour (no fallback).
    model_name: str | None = None
    # Phase N.3 — per-session USD cap enforced by GAPT's invoke
    # handler. `None` = no cap (free mode — opt-in by leaving the
    # session's `cost_budget_usd` unset). When set, the next invoke
    # rejects with `session.budget_exhausted` once the cumulative
    # `accumulator.cost_usd` crosses this number. geny-executor's
    # own `--max-budget-usd` flag is no longer wired up so the agent
    # never sees budget metadata in its prompt context.
    cost_budget_usd: float | None = None
    # Phase L.1 — geny-executor's PipelineState carried across every
    # `run_stream()` call on this session. Holds `session_id` (so the
    # executor's SESSION_* hooks group correctly) and `messages` —
    # the canonical Anthropic-format conversation array that stages
    # 1 (input) / 6 (api) / 10 (tool) append to. Without this, each
    # invoke spawned a fresh PipelineState and the agent saw only
    # the current turn (no memory). Lazy-init in `_drive_pipeline`
    # so test paths that don't go through the real pipeline don't
    # have to construct one.
    conversation_state: PipelineState | None = None
    # Phase M.1 — wall-clock-monotonic timestamp of last touch (invoke /
    # stream subscribe / cache hit). The `SessionRegistry` idle sweep
    # reads this to decide whether the runtime can be evicted. Set
    # initially to "now" so a freshly-built runtime isn't eligible
    # for eviction the moment the sweep next runs.
    last_active_at: float = field(default_factory=time.monotonic)
    # Phase M.1 — hard cap on `conversation_state.messages` entries.
    # `_drive_pipeline` trims the head after each `run_stream()` so the
    # next invoke doesn't push the model's context window. Operator-
    # tunable via `GAPT_SESSION_MAX_MESSAGES_IN_STATE`. Default mirrors
    # the Settings default — runtimes built without a container (test
    # paths) keep the same ceiling so unit tests exercise the trim.
    max_state_messages: int = 50
    # Phase M.2 (re-based on geny-executor 2.2.0 `ModelOverrides`) —
    # the session's *sticky* override selection. The pre-2.2 code
    # mutated `pipeline._config.model.*` with a hand-rolled baseline
    # capture/revert dance; 2.2.0's per-run `overrides=` kwarg makes
    # that obsolete: `_drive_pipeline` passes these fields as a
    # `ModelOverrides` on every `run_stream`, the executor applies
    # them for exactly that run, and "clear" is simply setting the
    # field back to None (the next run reverts to manifest values
    # automatically — no revert bookkeeping).
    override_model: str | None = None
    override_thinking_enabled: bool | None = None
    override_thinking_budget_tokens: int | None = None
    # The manifest's bundled api-stage model — used as the pricing-
    # fallback key when an override model is cleared, and surfaced by
    # the chat panel's "inherit (uses X)" pill. Set at runtime
    # construction by `_build_runtime_from_handle`.
    _baseline_model: str | None = None
    _task: asyncio.Task[None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        """Reset the idle clock — called on every meaningful access
        (`SessionRegistry.get` / `register`)."""
        self.last_active_at = time.monotonic()

    def apply_per_invoke_overrides(
        self,
        *,
        model: str | None,
        thinking_enabled: bool | None,
        thinking_budget_tokens: int | None,
        clear: list[str] | None,
    ) -> None:
        """Record the session's override selection. Nothing on the
        pipeline is mutated — `_drive_pipeline` translates the stored
        fields into a one-run `geny_executor.ModelOverrides` per
        `run_stream` call, so values persist across invokes until
        another override or a `clear` request resets them (the UX the
        chat panel promises) while the executor sees only sanctioned
        per-run overrides.

        `clear` lists override names ("model", "thinking_enabled",
        "thinking_budget_tokens", or "thinking" as a shortcut for the
        two thinking_* fields) to revert to the manifest baseline.
        Reset overrides win over set values in the same request — the
        UI's "clear" button shouldn't have to also blank the input.
        """
        any_override = (
            model is not None
            or thinking_enabled is not None
            or thinking_budget_tokens is not None
            or bool(clear)
        )
        if not any_override:
            return
        clear_set = {c.strip().lower() for c in (clear or []) if isinstance(c, str)}
        # "thinking" is a UX shortcut — most chat surfaces present
        # thinking as a single toggle + slider pair, not two independent
        # fields, so a single "clear thinking" request resets both.
        if "thinking" in clear_set:
            clear_set.update({"thinking_enabled", "thinking_budget_tokens"})

        if "model" in clear_set:
            self.override_model = None
            if self._baseline_model is not None:
                self.model_name = self._baseline_model
        elif model is not None:
            self.override_model = model
            self.model_name = model

        if "thinking_enabled" in clear_set:
            self.override_thinking_enabled = None
        elif thinking_enabled is not None:
            self.override_thinking_enabled = thinking_enabled

        if "thinking_budget_tokens" in clear_set:
            self.override_thinking_budget_tokens = None
        elif thinking_budget_tokens is not None:
            self.override_thinking_budget_tokens = thinking_budget_tokens
            # Operator convenience: budget > 0 + no explicit enable
            # flips thinking on. Same heuristic the manifest-time
            # `apply_overrides` uses.
            if (
                thinking_enabled is None
                and "thinking_enabled" not in clear_set
                and thinking_budget_tokens > 0
            ):
                self.override_thinking_enabled = True

        logger.info(
            "session.override.applied",
            session_id=self.session_id,
            request_model=model,
            request_thinking_enabled=thinking_enabled,
            request_thinking_budget_tokens=thinking_budget_tokens,
            request_clear=list(clear_set) if clear_set else None,
            effective_model=self.override_model,
            effective_thinking_enabled=self.override_thinking_enabled,
            effective_thinking_budget=self.override_thinking_budget_tokens,
            baseline_model=self._baseline_model,
        )

    def pending_model_overrides(self) -> ModelOverrides | None:
        """Translate the stored override selection into the executor's
        per-run `ModelOverrides`, or None when nothing is overridden
        (saves the executor a no-op apply + `config.override_applied`
        event spam)."""
        if (
            self.override_model is None
            and self.override_thinking_enabled is None
            and self.override_thinking_budget_tokens is None
        ):
            return None
        from geny_executor import ModelOverrides  # noqa: PLC0415 — keep test paths import-light

        return ModelOverrides(
            model=self.override_model,
            thinking_enabled=self.override_thinking_enabled,
            thinking_budget_tokens=self.override_thinking_budget_tokens,
        )

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def invoke(
        self,
        message: str,
        *,
        runner: SessionInvokeRunner | None = None,
        mode: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        """Kick off a background task that runs the pipeline against
        `message` and publishes SSE events. Returns once the task is
        scheduled. The task lifetime tracks the SSE stream lifetime —
        callers should `await` the task only via `wait_done()` (which
        also handles the cancellation path).

        Phase D.1 — when ``mode`` is provided ("plan" or "act") and a
        ``mode_ref`` was attached at construction, the runtime updates
        the reference *before* spawning the task so the policy hook
        sees the new mode on the first tool call. Unknown values are
        ignored (default mode stays).
        """
        # Phase M.1 — every invoke counts as activity. The registry's
        # idle sweep reads `last_active_at` so a chatting user doesn't
        # have their runtime evicted out from under the next turn.
        self.touch()
        if mode is not None and self.mode_ref is not None and mode in ("plan", "act"):
            self.mode_ref.mode = mode
        # Stash attachments on the runtime instead of widening the
        # runner callable — `SessionInvokeRunner` is a public seam
        # that existing tests stub with a bare `(runtime, message)`
        # signature. `_drive_pipeline` pops the stash exactly once.
        self._pending_attachments = list(attachments) if attachments else None
        async with self._lock:
            if self.is_running:
                raise SessionAlreadyInvoking(f"session {self.session_id} is already invoking")
            actual_runner = runner or _default_invoke_runner
            self._task = asyncio.create_task(
                _run_with_lifecycle(self, message, actual_runner),
                name=f"session-invoke-{self.session_id}",
            )

    def take_pending_attachments(self) -> list[dict[str, Any]] | None:
        """Pop this turn's attachments (exactly-once semantics — a
        crash-retry of the same runtime must not resend stale
        images)."""
        pending = getattr(self, "_pending_attachments", None)
        self._pending_attachments = None
        return pending

    async def wait_done(self) -> None:
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def interrupt(self, *, wait: bool = False) -> bool:
        """Cancel the running invoke task if any. Returns True when an
        active task was cancelled.

        Phase N.2.7 — when ``wait=True``, blocks until the cancelled
        task's lifecycle handler has emitted its terminal ERROR/DONE
        frame and the bus is settled. Used by the route layer so a
        subsequent ``/invoke`` retry never races the cleanup and gets
        a stale 409 ``session.already_invoking``. Default stays
        ``False`` for legacy callers that fire-and-forget."""
        async with self._lock:
            if not self.is_running or self._task is None:
                return False
            self._task.cancel()
            task_ref = self._task
        if wait:
            # `wait_done` already swallows CancelledError; calling it
            # after dropping the lock so a slow finally-block on the
            # cancelled task doesn't hold up another runtime caller.
            with contextlib.suppress(asyncio.CancelledError):
                await task_ref
        return True

    async def aclose(self) -> None:
        await self.interrupt()
        await self.wait_done()
        await self.bus.close()
        # geny-executor 2.2.0 — teardown is owed by the host: aclose()
        # cancels pending HITL futures, closes events() taps,
        # disconnects MCP servers (reaping stdio children), and shuts
        # down tool providers. Without this, every LRU/idle eviction
        # leaked an MCP child process per session. Guarded because
        # test runtimes carry stub pipelines without the method.
        pipeline_aclose = getattr(self.pipeline, "aclose", None)
        if pipeline_aclose is not None:
            try:
                await pipeline_aclose()
            except Exception:  # teardown is best-effort
                logger.exception(
                    "session.pipeline_aclose_failed", session_id=self.session_id
                )


async def _run_with_lifecycle(
    runtime: SessionRuntime, message: str, runner: SessionInvokeRunner
) -> None:
    """Wraps the runner so we always publish a terminal `done` or
    `error` event regardless of how the task ends."""
    # Phase I.2 — record the user's prompt as the FIRST event of this
    # turn so the transcript archive has both sides. `_run_with_lifecycle`
    # is the right spot (not `invoke()`): we're inside the spawned task,
    # the bus persister is wired, and no executor frames have landed yet
    # so seq ordering reads as user → text → tool → done.
    user_payload: dict[str, object] = {"text": message}
    pending_meta = getattr(runtime, "_pending_attachments", None)
    if pending_meta:
        user_payload["attachments"] = [
            {"kind": a.get("kind", "image"), "media_type": a.get("mime_type", "")}
            for a in pending_meta
        ]
    await runtime.bus.publish(SessionEventKind.USER_MESSAGE, user_payload)
    try:
        await runner(runtime, message)
    except asyncio.CancelledError:
        # Park the cancellation so the next await isn't auto-cancelled,
        # publish the user-visible error frame, then re-raise so the
        # task still ends with CancelledError state.
        current = asyncio.current_task()
        if current is not None:
            current.uncancel()
        await runtime.bus.publish(
            SessionEventKind.ERROR,
            {"exec_code": "exec.session.cancelled", "reason": "invoke cancelled"},
        )
        raise
    except Exception as exc:
        logger.exception("session.invoke_crashed", session_id=runtime.session_id)
        await runtime.bus.publish(
            SessionEventKind.ERROR,
            {
                "exec_code": "exec.session.crashed",
                "reason": f"{type(exc).__name__}: {exc}"[:400],
            },
        )
    else:
        await runtime.bus.publish(
            SessionEventKind.DONE,
            {"cost": runtime.accumulator.snapshot()},
        )


async def _default_invoke_runner(runtime: SessionRuntime, message: str) -> None:
    # Sandbox routing no longer needs a ContextVar dance: the runtime's
    # pipeline carries a `ClaudeCodeCLIClient(runner_factory=...)`
    # attached at construction time (see `agent/sandbox_runner.py` +
    # `routers/sessions._build_runtime_from_handle`), so every CLI
    # spawn for this session already targets the right container.
    await _drive_pipeline(runtime, message)


async def _drive_pipeline(runtime: SessionRuntime, message: str) -> None:
    """Drives `Pipeline.run_stream(message)` and maps each
    `PipelineEvent` onto a SessionEvent.

    Event names come from geny-executor 2.2.0's owned `EventTypes`
    catalogue (events.md) — no more empirically-derived strings. The
    streaming chunk set Stage 6 forwards since 2.2.0 (previously only
    text deltas; the rest died inside the stage and GAPT monkey-patched
    `_call_streaming` to see them):

      text.delta              — `{"text"}` *** chat text ***
      thinking.delta          — `{"text"}` extended-thinking beats
      api.tool_use            — `{id, name, input, source: cli|api}`
      api.cli_tool_call       — narrow companion (source == cli only)
      api.input_json_delta    — partial tool-input JSON fragments
      api.content_block_stop  — block boundary marker
      api.tool_result         — `{tool_use_id, content, is_error, source}`
      api.error               — `{code, category, provider, message}`

    plus the long-standing lifecycle set (pipeline.* / stage.* /
    token.tracked / parse.complete / evaluate.* / loop.* / ...).

    Phase L.1 — pass `runtime.conversation_state` into `run_stream` so
    the executor preserves prior turns' messages (officially supported
    in 2.2.0: `begin_turn()` resets per-turn fields — iteration,
    loop_decision, in-flight tool work, per-turn `total_cost_usd` —
    when a reused state re-enters the run). The state object is
    mutated in place by stage 1 (input append-user) and stage 6 (api
    append-assistant); we keep the same reference on the runtime so
    the next invoke sees the accumulated history.

    Phase M.2 (2.2.0) — the session's sticky model/thinking override
    selection is handed to the executor as a sanctioned one-run
    `ModelOverrides` (`overrides=` kwarg) instead of mutating
    `pipeline._config.model.*` behind its back.
    """
    if runtime.conversation_state is None:
        # Lazy-import the executor's state class so test paths that
        # mock `runtime.pipeline` don't have to pull the executor in.
        from geny_executor.core.state import PipelineState  # noqa: PLC0415

        runtime.conversation_state = PipelineState(
            session_id=runtime.session_id,
        )

    # Phase M.1 — trim `state.messages` BEFORE the run so the executor
    # builds its context window against a bounded array. We trim from
    # the head (oldest first) so the most-recent turns the user is
    # actively referring to are preserved. Stage 1 (input) will append
    # this turn's user message after the trim, so the cap applies to
    # the prior history we carry forward.
    state = runtime.conversation_state
    cap = max(1, runtime.max_state_messages)
    if state is not None and len(state.messages) > cap:
        overflow = len(state.messages) - cap
        state.messages = state.messages[overflow:]
        logger.info(
            "session.state.trimmed",
            session_id=runtime.session_id,
            dropped=overflow,
            kept=len(state.messages),
        )

    # Multimodal turn: the executor's input stage (s01) auto-routes a
    # dict with an `attachments` key through its MultimodalNormalizer,
    # which converts the lenient `{kind, mime_type, data}` form into
    # Anthropic image content blocks — the model genuinely sees the
    # image. Text-only turns keep passing the bare string so every
    # legacy test stub stays valid.
    attachments = runtime.take_pending_attachments()
    run_input: Any = (
        {"text": message, "attachments": attachments} if attachments else message
    )

    overrides = runtime.pending_model_overrides()
    if overrides is not None:
        stream = runtime.pipeline.run_stream(
            run_input, state=runtime.conversation_state, overrides=overrides
        )
    else:
        # No-override calls skip the kwarg entirely so legacy test
        # stubs with a bare `run_stream(message, state=None)` signature
        # keep working.
        stream = runtime.pipeline.run_stream(run_input, state=runtime.conversation_state)

    async for ev in stream:
        event_type = str(getattr(ev, "type", ""))
        stage_name = getattr(ev, "stage", "") or ""
        data: dict[str, object] = dict(getattr(ev, "data", {}) or {})

        # Token + cost accounting: token.tracked carries the real numbers.
        # Feed them into the cost accumulator so the snapshot the
        # lifecycle wrapper emits in DONE has non-zero totals.
        #
        # Phase I.1 — route through `runtime.cost_callback` instead of
        # publishing COST directly here. The router-supplied callback
        # owns three responsibilities the bare publish was skipping:
        #   1. COST SSE frame (for the live chat header)
        #   2. AgentSession DB row update (cost_usd / tokens columns)
        #   3. Prometheus counter inc for cost / tokens
        # Pre-I.1 the DB write only ran via the POST_TOOL_USE hook, so
        # tool-less chat sessions stayed at $0.0000 forever even though
        # the in-memory accumulator had the right numbers.
        if event_type == EventTypes.TOKEN_TRACKED:
            _update_accumulator(runtime, data)
            if runtime.cost_callback is not None:
                await runtime.cost_callback(runtime.accumulator)
            else:
                # Fallback for legacy paths (tests without a router-wired
                # callback) — preserve the old behaviour of at least
                # emitting the SSE frame so the chat header still ticks.
                await runtime.bus.publish(
                    SessionEventKind.COST, runtime.accumulator.snapshot()
                )
            continue

        # Pipeline trace: forward the executor's verbose stage events
        # as `step` frames so the chat UI's "과정" panel can show
        # what the agent is doing in near-real-time. This is *separate*
        # from the chat-text path — text/tool_call still fire below.
        step_payload = _maybe_step_payload(event_type, stage_name, data)
        if step_payload is not None:
            await runtime.bus.publish(SessionEventKind.STEP, step_payload)

        # Tool invocations — `api.tool_use` (2.2.0 built-in chunk
        # forwarding; pre-2.2 this required GAPT's `_call_streaming`
        # fork). Fires for BOTH CLI-executed tools (source == "cli":
        # Bash / Read / Edit the claude CLI ran itself) and API-side
        # tool requests (source == "api": Stage 10 will dispatch).
        # Forwarded as a TOOL_CALL frame so the ToolCallCard renders
        # inline in the chat — the gap the old patch docstring called
        # out ("the GAPT chat trace has no idea any tool ran").
        if event_type == EventTypes.API_TOOL_USE:
            await runtime.bus.publish(
                SessionEventKind.TOOL_CALL,
                {
                    "tool": data.get("name"),
                    "tool_name": data.get("name"),
                    "tool_use_id": data.get("id"),
                    "input": data.get("input"),
                    "source": data.get("source"),
                },
            )
            continue
        # `api.cli_tool_call` duplicates `api.tool_use` for narrow
        # subscribers; we consume the broad event above, so drop the
        # companion (it would double-render every ToolCallCard) —
        # likewise the raw streaming bookkeeping frames.
        if event_type in (
            EventTypes.API_CLI_TOOL_CALL,
            EventTypes.API_INPUT_JSON_DELTA,
            EventTypes.API_CONTENT_BLOCK_STOP,
        ):
            continue
        # Tool result (paired with api.tool_use by tool_use_id) —
        # canonical since 2.2.0 (the CLI translator now surfaces the
        # synthetic user-role tool_result echoes the old
        # `StreamJsonAccumulator.feed` patch reverse-engineered).
        # Without this the ToolCallCard stays in "running" forever.
        if event_type == EventTypes.API_TOOL_RESULT:
            content = _flatten_tool_result_content(data.get("content"))
            await runtime.bus.publish(
                SessionEventKind.TOOL_RESULT,
                {
                    "tool_use_id": data.get("tool_use_id"),
                    "is_error": data.get("is_error", False),
                    "output": content,
                    "content": content,
                },
            )
            continue

        kind, payload = _map_pipeline_event(event_type, data)
        if kind is None:
            continue
        await runtime.bus.publish(kind, payload)


def _flatten_tool_result_content(raw: object) -> str:
    """`api.tool_result.content` arrives "as the backend reported it" —
    a string, or a list of text/image blocks (Anthropic shape). Flatten
    to a single string so the UI renders it in one row (same behaviour
    the old feed-patch implemented)."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for c in raw:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text", "")))
            elif isinstance(c, str):
                parts.append(c)
        return "".join(parts)
    return ""


# Stage events to forward as `step` frames. Whitelisted so we don't
# spam the UI with internal-only chatter; each entry maps an event
# type → human-readable phase + which interesting payload field to
# keep. `summary` is freeform; the front-end uses the `phase` field
# (one of {stage_enter,stage_exit,stage_bypass,api,parse,evaluate,
# loop,yield}) to bucket / colour. Missing keys leave the payload
# minimal.
_STEP_EVENT_MAP: dict[str, str] = {
    EventTypes.STAGE_ENTER: "stage_enter",
    EventTypes.STAGE_EXIT: "stage_exit",
    EventTypes.STAGE_BYPASS: "stage_bypass",
    EventTypes.API_REQUEST: "api_request",
    EventTypes.API_RESPONSE: "api_response",
    EventTypes.PARSE_COMPLETE: "parse",
    EventTypes.EVALUATE_START: "evaluate_start",
    EventTypes.EVALUATE_COMPLETE: "evaluate_complete",
    EventTypes.LOOP_COMPLETE: "loop",
    EventTypes.YIELD_COMPLETE: "yield",
    EventTypes.GUARD_CHECK: "guard",
    EventTypes.CONTEXT_BUILT: "context",
    EventTypes.SYSTEM_BUILT: "system",
    EventTypes.MEMORY_UPDATED: "memory",
    EventTypes.TASK_REGISTRY_SYNCED: "task_registry",
    EventTypes.INPUT_NORMALIZED: "input",
    # 2.2.0 built-in chunk forwarding — fires whenever the CLI runs a
    # tool internally (Bash / Read / Edit / ...) or the API requests
    # one. Gives the user agentic-flow visibility AND lets the
    # ToolCallCard flip from "running" to "complete" once the tool
    # returns. (Phase names kept stable for the web UI's buckets.)
    EventTypes.API_TOOL_USE: "tool_invoke",
    EventTypes.API_TOOL_RESULT: "tool_result",
    EventTypes.THINKING_DELTA: "thinking",
    # Per-attempt API error envelope ({code, category, provider,
    # message}) — terminal failures still arrive as pipeline.error;
    # this row surfaces retried/raw errors in the trace.
    EventTypes.API_ERROR: "api_error",
}


def _maybe_step_payload(
    event_type: str, stage: str, data: dict[str, object]
) -> dict[str, object] | None:
    """Translate an executor pipeline event into a compact `step`
    payload, or return None when the event isn't trace-worthy.

    The UI gets just enough to render one collapsible row per stage
    crossing — `stage`, `phase`, and a short `summary` derived from
    the data payload (model name for API requests, tool count for
    parse, etc.). Heavy data (full text, prompt content) is *not*
    forwarded — that's the role of `text` / `tool_result`."""
    phase = _STEP_EVENT_MAP.get(event_type)
    if phase is None:
        return None
    summary = ""
    if event_type == EventTypes.API_REQUEST:
        model = data.get("model")
        provider = data.get("provider")
        summary = f"{provider} / {model}" if model else ""
    elif event_type == EventTypes.API_RESPONSE:
        in_t = data.get("input_tokens")
        out_t = data.get("output_tokens")
        if in_t is not None or out_t is not None:
            summary = f"in={in_t} out={out_t}"
    elif event_type == EventTypes.PARSE_COMPLETE:
        tc = data.get("tool_calls", 0)
        tl = data.get("text_length", 0)
        summary = f"text={tl}, tools={tc}"
    elif event_type == EventTypes.GUARD_CHECK:
        if data.get("passed") is False:
            summary = f"FAIL: {data.get('message', '')}"
    elif event_type == EventTypes.YIELD_COMPLETE:
        summary = f"iters={data.get('iterations', 0)}"
    elif event_type == EventTypes.LOOP_COMPLETE:
        summary = f"iter={data.get('iteration', 0)}"
    elif event_type == EventTypes.CONTEXT_BUILT:
        summary = f"msgs={data.get('message_count', 0)}"
    elif event_type == EventTypes.API_ERROR:
        code = data.get("code", "")
        message = str(data.get("message", ""))
        msg_short = message if len(message) <= 80 else message[:77] + "…"
        summary = f"{code} {msg_short}".strip()
    elif event_type == EventTypes.API_TOOL_USE:
        name = data.get("name", "tool")
        # The first input arg gives the most useful hint at a glance —
        # `Bash {command: "ls -la"}` becomes "Bash · ls -la".
        input_payload = data.get("input")
        hint = ""
        if isinstance(input_payload, dict) and input_payload:
            # Prefer keys users care about, else the first value.
            for key in ("command", "file_path", "path", "pattern", "query", "url"):
                if key in input_payload:
                    hint = str(input_payload[key])
                    break
            if not hint:
                first_val = next(iter(input_payload.values()))
                hint = str(first_val)
        if hint:
            # Trim long shell commands so the trace row stays one line.
            hint_short = hint if len(hint) <= 60 else hint[:57] + "…"
            summary = f"{name} · {hint_short}"
        else:
            summary = str(name)
    elif event_type == EventTypes.THINKING_DELTA:
        # No summary; the presence of the row is the signal.
        summary = ""

    return {
        "phase": phase,
        "stage": stage,
        "event": event_type,
        "summary": summary,
    }


def _update_accumulator(runtime: SessionRuntime, data: dict[str, object]) -> None:
    """Apply a `token.tracked` payload to the runtime's CostAccumulator.

    Executor payload shape: `{"input_tokens", "output_tokens",
    "cache_write", "cache_read", "cost_usd", "total_cost_usd"}`. We
    treat each `token.tracked` as a delta (the executor emits one per
    API call) so cumulative totals are the sum of deltas. NOTE
    (2.2.0): the payload's `total_cost_usd` is a *per-turn*
    accumulator now (`begin_turn()` resets it; the session-cumulative
    figure moved to `state.session_cost_usd`). GAPT never reads
    either — only the per-call `cost_usd` delta — so the 2.2.0
    semantics change is a no-op here; the session-cumulative source
    of truth stays GAPT's own `CostAccumulator` (DB-seeded across
    restarts).

    Phase I.3 — when the payload's own `cost_usd` is 0 but tokens are
    positive (the model-alias gap: manifest says `"model":"sonnet"`,
    upstream's pricing dict only has canonical ids), fall back to
    GAPT's own pricing resolver. The fallback uses upstream's pricing
    table for the dollar values — only the alias-resolution layer is
    GAPT-owned (see `agent/pricing.py`)."""
    acc = runtime.accumulator
    in_t = data.get("input_tokens")
    in_delta = in_t if isinstance(in_t, int) else 0
    if in_delta:
        acc.input_tokens += in_delta
    out_t = data.get("output_tokens")
    out_delta = out_t if isinstance(out_t, int) else 0
    if out_delta:
        acc.output_tokens += out_delta

    # Phase K.2 — track Anthropic cache tokens. The executor's
    # `token.tracked` payload uses the bare keys `cache_write` and
    # `cache_read` (not `*_tokens` — matches the Anthropic API names
    # `cache_creation_input_tokens` / `cache_read_input_tokens` once
    # the executor strips the long form).
    cache_write_raw = data.get("cache_write")
    cache_write_delta = cache_write_raw if isinstance(cache_write_raw, int) else 0
    if cache_write_delta:
        acc.cache_write_tokens += cache_write_delta
    cache_read_raw = data.get("cache_read")
    cache_read_delta = cache_read_raw if isinstance(cache_read_raw, int) else 0
    if cache_read_delta:
        acc.cache_read_tokens += cache_read_delta

    cost = data.get("cost_usd")
    cost_value = float(cost) if isinstance(cost, int | float) else 0.0
    if (
        cost_value == 0.0
        and (in_delta or out_delta or cache_write_delta or cache_read_delta)
        and runtime.model_name
    ):
        from gapt_server.agent.pricing import compute_cost_usd  # noqa: PLC0415

        cost_value = compute_cost_usd(
            model=runtime.model_name,
            input_tokens=in_delta,
            output_tokens=out_delta,
            cache_write=cache_write_delta,
            cache_read=cache_read_delta,
        )
        if cost_value == 0.0:
            # Pricing entry truly missing — warn once so an operator
            # noticing the cost dashboard staying at $0.0000 has a
            # log breadcrumb to start from.
            logger.warning(
                "agent.pricing.fallback_missing",
                session_id=runtime.session_id,
                model=runtime.model_name,
            )
    if cost_value:
        acc.cost_usd += cost_value


def _map_pipeline_event(  # noqa: PLR0911 — 7-way taxonomy reads cleaner as straight returns
    event_type: str,
    data: dict[str, object],
) -> tuple[SessionEventKind | None, dict[str, object]]:
    """Map a `geny_executor.PipelineEvent` → (kind, payload). Unknown
    event types map to `None` so the stream stays compact."""
    # Suppressed envelopes — wrapper handles the lifecycle.
    if event_type in {
        EventTypes.PIPELINE_START,
        EventTypes.STAGE_ENTER,
        EventTypes.STAGE_EXIT,
        EventTypes.STAGE_BYPASS,
    }:
        return None, {}
    if event_type == EventTypes.PIPELINE_COMPLETE:
        # Final text already streamed via `text.delta`; the wrapper
        # emits DONE with the accumulator snapshot afterwards.
        return None, {}
    if event_type == EventTypes.PIPELINE_ERROR:
        return SessionEventKind.ERROR, data

    # Chat text — `text.delta` carries `{"text": "<chunk>"}` straight
    # from the CLI's stream-json. Keep the chunk as-is so the UI can
    # append without re-parsing. The legacy `*.chunk` / `text`
    # patterns stay matched so test stubs and other providers that
    # use the older event names still surface.
    if (
        event_type == EventTypes.TEXT_DELTA
        or event_type == "text"
        or event_type.endswith(".chunk")
    ):
        return SessionEventKind.TEXT, {"text": data.get("text", "")}

    # Stage 10 dispatch — `tool.execute_start` / `tool.execute_complete`
    # (plus host/test stubs still using `tool.call` / `tool.error`
    # shapes). Map both halves so ToolCallCard can render the
    # request-response pair. The Stage 6 streaming announcements
    # (`api.tool_use` / `api.tool_result`) are handled explicitly in
    # `_drive_pipeline` before this fallback runs.
    if "tool" in event_type and ("result" in event_type or "complete" in event_type or "error" in event_type):
        return SessionEventKind.TOOL_RESULT, data
    if "tool" in event_type and ("call" in event_type or "invoke" in event_type or "start" in event_type):
        return SessionEventKind.TOOL_CALL, data

    return None, {}


@dataclass
class SessionRegistry:
    """Stateless-ish lookup by session_id with LRU + idle eviction.

    Phase M.1 — the previous version held every rehydrated runtime
    forever; a 24h-uptime server accumulated entries indefinitely.
    The new contract:

    1. The entries table is an `OrderedDict` and `get()` moves the
       hit to the most-recently-used end. `register()` does the same
       for fresh inserts.
    2. When `register()` would push the entry count above
       `max_size`, the oldest is popped + `aclose()`-ed BEFORE the
       new entry lands. The pop happens under the lock; the close
       is awaited *outside* the lock so a slow aclose doesn't block
       other sessions.
    3. The `_idle_sweep` loop runs every `sweep_interval_s` seconds
       and evicts any runtime whose `last_active_at` is older than
       `idle_eviction_s`. Active sessions bump `touch()` on every
       invoke / SSE subscribe so they never starve.

    Subsequent activity on an evicted session triggers the normal
    rehydrate-from-DB path (Phase L.1 contract) — no memory loss,
    just a small extra latency on the first event.
    """

    _entries: OrderedDict[str, SessionRuntime] = field(
        default_factory=OrderedDict
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Defaults match `Settings.session_runtime_cache_size` /
    # `session_runtime_idle_eviction_s`; the container override
    # plumbs the operator-tuned values through at startup.
    max_size: int = 50
    idle_eviction_s: float = 1800.0
    # How often the background sweep checks for idle eviction. Kept
    # tight (60s) so idle eviction fires close to its nominal time;
    # the work itself is cheap (one timestamp compare per entry).
    sweep_interval_s: float = 60.0
    _sweep_task: asyncio.Task[None] | None = field(default=None)

    def configure(
        self,
        *,
        max_size: int,
        idle_eviction_s: float,
    ) -> None:
        """Called by the container builder once `Settings` is loaded
        so the registry honours operator-tuned caps rather than the
        dataclass defaults."""
        self.max_size = max(1, max_size)
        self.idle_eviction_s = float(idle_eviction_s)

    def start_sweep(self) -> None:
        """Spawn the idle-sweep background task. Called from
        `app.lifespan` after the container is wired so the loop runs
        for the server's lifetime. Safe to call twice — a second
        call no-ops if a task is already alive."""
        if self._sweep_task is not None and not self._sweep_task.done():
            return
        self._sweep_task = asyncio.create_task(
            self._idle_sweep(), name="session-registry-sweep"
        )

    async def register(self, runtime: SessionRuntime) -> None:
        evicted: SessionRuntime | None = None
        async with self._lock:
            # Move-to-end on re-register so the entry counts as fresh.
            if runtime.session_id in self._entries:
                self._entries.move_to_end(runtime.session_id)
            self._entries[runtime.session_id] = runtime
            runtime.touch()
            if len(self._entries) > self.max_size:
                # popitem(last=False) returns + removes the LRU entry.
                _evicted_id, evicted = self._entries.popitem(last=False)
        if evicted is not None:
            logger.info(
                "session.registry.lru_evict",
                session_id=evicted.session_id,
                reason="cache_full",
            )
            await evicted.aclose()

    async def get(self, session_id: str) -> SessionRuntime:
        async with self._lock:
            try:
                rt = self._entries[session_id]
            except KeyError as exc:
                raise SessionNotFound(session_id) from exc
            self._entries.move_to_end(session_id)
            rt.touch()
            return rt

    async def pop(self, session_id: str) -> SessionRuntime | None:
        async with self._lock:
            return self._entries.pop(session_id, None)

    async def invalidate_user(self, user_id: str) -> int:
        """Drop every cached runtime that belongs to `user_id` so the
        next invoke / stream forces a fresh `rehydrate_session` —
        picking up any prefs the user just saved (model, max_tokens,
        permission_mode, ...). Returns the number of runtimes
        evicted. The runtime's `aclose` is awaited *outside* the
        registry lock to avoid holding it during the close hand-off.
        """
        async with self._lock:
            to_drop = [
                rt for rt in self._entries.values() if rt.user_id == user_id
            ]
            for rt in to_drop:
                self._entries.pop(rt.session_id, None)
        for rt in to_drop:
            await rt.aclose()
        return len(to_drop)

    async def _idle_sweep(self) -> None:
        """Background loop — drops runtimes idle past the eviction
        window. Iterates a snapshot list so we can `aclose` outside
        the registry lock (same pattern as `invalidate_user`)."""
        while True:
            try:
                await asyncio.sleep(self.sweep_interval_s)
            except asyncio.CancelledError:
                return
            try:
                now = time.monotonic()
                async with self._lock:
                    stale = [
                        rt
                        for rt in self._entries.values()
                        if now - rt.last_active_at > self.idle_eviction_s
                    ]
                    for rt in stale:
                        self._entries.pop(rt.session_id, None)
                for rt in stale:
                    logger.info(
                        "session.registry.idle_evict",
                        session_id=rt.session_id,
                        idle_s=int(now - rt.last_active_at),
                    )
                    await rt.aclose()
            except Exception:  # noqa: BLE001 — sweep is best-effort
                logger.exception("session.registry.sweep_crashed")

    async def aclose(self) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweep_task
            self._sweep_task = None
        async with self._lock:
            runtimes = list(self._entries.values())
            self._entries.clear()
        for runtime in runtimes:
            await runtime.aclose()


__all__ = [
    "SessionAlreadyInvoking",
    "SessionInvokeRunner",
    "SessionNotFound",
    "SessionRegistry",
    "SessionRuntime",
]


DEFAULT_KEEPALIVE_S = 15.0


# Re-export for type-checker friendliness in callers.
async def stream_to_async_iter(
    runtime: SessionRuntime,
    *,
    replay_since: int | None = None,
    # Resolved at call time (not def time) so test paths can monkeypatch
    # `DEFAULT_KEEPALIVE_S` to force a fast flush through the in-memory
    # ASGI transport. Production paths still hit the 15s default.
    keepalive_s: float | None = None,
    prefix_events: list["SessionEvent"] | None = None,
) -> AsyncIterator[bytes]:
    """Yield SSE frames suitable for a `StreamingResponse(... media_type=\"text/event-stream\")`.

    Replays events with `seq > replay_since` first (if requested), then
    subscribes to live events. Honors back-pressure via the per-listener
    queue, sends a keepalive ``:keepalive`` comment every ``keepalive_s``
    seconds so proxies don't close the socket. Tests override the
    timeout to exercise the keepalive path without waiting 15 s.

    `prefix_events`, when provided, is yielded before the bus replay /
    subscribe. The route layer uses it to mix in DB-backed events for
    rehydrated sessions whose in-memory ring buffer was wiped by the
    last server restart — without this fallback, picking an existing
    session in the chat panel showed a blank pane until a fresh invoke
    landed.
    """
    if keepalive_s is None:
        keepalive_s = DEFAULT_KEEPALIVE_S
    if prefix_events:
        for ev in prefix_events:
            yield ev.to_sse()
    # Phase L.1 fix-up — DO NOT early-return on terminal events.
    #
    # Pre-fix: after a `done` frame we returned and closed the SSE
    # socket. The client's `useSessionStream` `useEffect` depends on
    # `sessionId` only, so a follow-up invoke on the *same* session
    # didn't reopen the stream — turn 2's `text` frame existed in the
    # DB (proving multi-turn memory works) but never reached the
    # browser. The user saw a frozen "단계 진입 · yield" instead of
    # the agent's reply.
    #
    # The new contract: keep the SSE stream alive for the session's
    # entire lifetime. Replay covers any pre-connection backlog; the
    # live loop blocks on the bus queue and forwards every future
    # frame, including the `done`/`text`/... pairs of subsequent
    # turns. The stream exits only when the bus is closed (session
    # archive / runtime aclose). The browser's EventSource never
    # sees an onerror, so the "Stream interrupted" banner stays away.
    if replay_since is not None:
        for past in await runtime.bus.replay(replay_since):
            yield past.to_sse()

    # Phase M.1 — SSE subscribe counts as activity. An open chat
    # panel keeps the runtime warm even if no new invokes are firing.
    runtime.touch()
    queue = await runtime.bus.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_s)
            except TimeoutError:
                yield b": keepalive\n\n"
                continue
            if event is None:
                # Bus was closed — session is going away, exit cleanly.
                return
            yield event.to_sse()
    finally:
        await runtime.bus.unsubscribe(queue)
