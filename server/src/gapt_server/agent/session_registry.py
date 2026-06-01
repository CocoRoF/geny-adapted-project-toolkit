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
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from gapt_server.agent.streaming import (
    SessionEvent,
    SessionEventBus,
    SessionEventKind,
)

if TYPE_CHECKING:
    from geny_executor import Pipeline
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
    # runtime is built. None for tests / paths that haven't been
    # migrated yet; the invoke runner falls back to host execution.
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
    _task: asyncio.Task[None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def invoke(
        self,
        message: str,
        *,
        runner: SessionInvokeRunner | None = None,
        mode: str | None = None,
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
        if mode is not None and self.mode_ref is not None and mode in ("plan", "act"):
            self.mode_ref.mode = mode
        async with self._lock:
            if self.is_running:
                raise SessionAlreadyInvoking(f"session {self.session_id} is already invoking")
            actual_runner = runner or _default_invoke_runner
            self._task = asyncio.create_task(
                _run_with_lifecycle(self, message, actual_runner),
                name=f"session-invoke-{self.session_id}",
            )

    async def wait_done(self) -> None:
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def interrupt(self) -> bool:
        """Cancel the running invoke task if any. Returns True when an
        active task was cancelled."""
        async with self._lock:
            if not self.is_running or self._task is None:
                return False
            self._task.cancel()
        return True

    async def aclose(self) -> None:
        await self.interrupt()
        await self.wait_done()
        await self.bus.close()


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
    await runtime.bus.publish(SessionEventKind.USER_MESSAGE, {"text": message})
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
    # Bind the workspace sandbox to this task's ContextVar so the
    # patched `CLIProcessRunner._spawn` (see `executor_patches.py`)
    # re-routes every claude CLI invocation through `docker exec
    # <gapt-ws-…>`. The token-reset in the finally restores the
    # outer value so concurrent sessions can't bleed sandbox state
    # into one another.
    from gapt_server.agent import (
        executor_patches,
    )

    token = executor_patches.set_current_sandbox(runtime.sandbox)
    try:
        await _drive_pipeline(runtime, message)
    finally:
        executor_patches.reset_current_sandbox(token)


async def _drive_pipeline(runtime: SessionRuntime, message: str) -> None:
    """Drives `Pipeline.run_stream(message)` and maps each
    `PipelineEvent` onto a SessionEvent.

    The executor's actual event taxonomy (as of geny-executor 2.1.0,
    empirically captured against the M0-P3 PoC):

      pipeline.start            — input snapshot
      stage.{enter,exit,bypass} — per-stage frames (suppressed)
      input.normalized          — text length only
      context.built             — message_count / tokens
      guard.check               — pass/fail
      api.request               — model, provider, etc. (no text)
      text.delta                — `{"text": "<chunk>"}` *** chat text ***
      api.response              — usage + lengths (no text)
      token.tracked             — tokens + cost_usd (real $) *** cost ***
      parse.complete            — text_length + tool_calls count
      tool.{call,invoke,result,…} — tool stage fine-grained events
      evaluate.* / loop.*       — iteration control
      yield.complete            — final text_length
      pipeline.complete         — `{"result": "<full text>", ...}`
      pipeline.error            — error envelope

    The previous mapping only matched `text` / `*.chunk` patterns —
    neither name is in the executor's vocabulary — so every text token
    was silently dropped. Same for cost: the cost_hook only fires on
    POST_TOOL_USE, so chat-without-tools sessions reported $0 forever.

    Phase L.1 — pass `runtime.conversation_state` into `run_stream` so
    the executor preserves prior turns' messages. Without this the
    pipeline rebuilt `state.messages = []` every invoke and the agent
    couldn't see what the user said two turns ago. The state object
    is mutated in place by stage 1 (input append-user) and stage 6
    (api append-assistant); we keep the same reference on the runtime
    so the next invoke sees the accumulated history.
    """
    if runtime.conversation_state is None:
        # Lazy-import the executor's state class so test paths that
        # mock `runtime.pipeline` don't have to pull the executor in.
        from geny_executor.core.state import PipelineState  # noqa: PLC0415

        runtime.conversation_state = PipelineState(
            session_id=runtime.session_id,
        )

    async for ev in runtime.pipeline.run_stream(
        message, state=runtime.conversation_state
    ):
        event_type = getattr(ev, "type", "")
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
        if event_type == "token.tracked":
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

        # CLI-side tool invocations (Bash / Read / Edit / ...) come
        # through the `tool.invoke` event our executor_patches shim
        # adds. Forward each one as a TOOL_CALL frame too so the
        # ToolCallCard renders inline in the chat.
        if event_type == "tool.invoke":
            await runtime.bus.publish(
                SessionEventKind.TOOL_CALL,
                {
                    "tool": data.get("name"),
                    "tool_name": data.get("name"),
                    "tool_use_id": data.get("tool_use_id"),
                    "input": data.get("input"),
                },
            )
            continue
        # Tool result (paired with tool.invoke by tool_use_id) — also
        # synthesised by the executor_patches shim from `user` lines
        # carrying tool_result blocks. Without this the ToolCallCard
        # stays in "running" forever.
        if event_type == "tool.result":
            await runtime.bus.publish(
                SessionEventKind.TOOL_RESULT,
                {
                    "tool_use_id": data.get("tool_use_id"),
                    "is_error": data.get("is_error", False),
                    "output": data.get("content", ""),
                    "content": data.get("content", ""),
                },
            )
            continue

        kind, payload = _map_pipeline_event(event_type, data)
        if kind is None:
            continue
        await runtime.bus.publish(kind, payload)


# Stage events to forward as `step` frames. Whitelisted so we don't
# spam the UI with internal-only chatter; each entry maps an event
# type → human-readable phase + which interesting payload field to
# keep. `summary` is freeform; the front-end uses the `phase` field
# (one of {stage_enter,stage_exit,stage_bypass,api,parse,evaluate,
# loop,yield}) to bucket / colour. Missing keys leave the payload
# minimal.
_STEP_EVENT_MAP: dict[str, str] = {
    "stage.enter": "stage_enter",
    "stage.exit": "stage_exit",
    "stage.bypass": "stage_bypass",
    "api.request": "api_request",
    "api.response": "api_response",
    "parse.complete": "parse",
    "evaluate.start": "evaluate_start",
    "evaluate.complete": "evaluate_complete",
    "loop.complete": "loop",
    "yield.complete": "yield",
    "guard.check": "guard",
    "context.built": "context",
    "system.built": "system",
    "memory.updated": "memory",
    "task_registry.synced": "task_registry",
    "input.normalized": "input",
    # `tool.invoke` / `tool.result` are added by our executor_patches
    # shim. They fire whenever the CLI runs a tool internally
    # (Bash / Read / Edit / ...) — gives the user agentic-flow
    # visibility AND lets the ToolCallCard flip from "running" to
    # "complete" once the tool returns.
    "tool.invoke": "tool_invoke",
    "tool.result": "tool_result",
    "thinking.delta": "thinking",
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
    if event_type == "api.request":
        model = data.get("model")
        provider = data.get("provider")
        summary = f"{provider} / {model}" if model else ""
    elif event_type == "api.response":
        in_t = data.get("input_tokens")
        out_t = data.get("output_tokens")
        if in_t is not None or out_t is not None:
            summary = f"in={in_t} out={out_t}"
    elif event_type == "parse.complete":
        tc = data.get("tool_calls", 0)
        tl = data.get("text_length", 0)
        summary = f"text={tl}, tools={tc}"
    elif event_type == "guard.check":
        if data.get("passed") is False:
            summary = f"FAIL: {data.get('message', '')}"
    elif event_type == "yield.complete":
        summary = f"iters={data.get('iterations', 0)}"
    elif event_type == "loop.complete":
        summary = f"iter={data.get('iteration', 0)}"
    elif event_type == "context.built":
        summary = f"msgs={data.get('message_count', 0)}"
    elif event_type == "tool.invoke":
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
    elif event_type == "thinking.delta":
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
    API call) so cumulative totals are the sum of deltas.

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
    if event_type in {"pipeline.start", "stage.enter", "stage.exit", "stage.bypass"}:
        return None, {}
    if event_type == "pipeline.complete":
        # Final text already streamed via `text.delta`; the wrapper
        # emits DONE with the accumulator snapshot afterwards.
        return None, {}
    if event_type == "pipeline.error":
        return SessionEventKind.ERROR, data

    # Chat text — `text.delta` carries `{"text": "<chunk>"}` straight
    # from the CLI's stream-json. Keep the chunk as-is so the UI can
    # append without re-parsing. The legacy `*.chunk` / `text`
    # patterns stay matched so test stubs and other providers that
    # use the older event names still surface.
    if (
        event_type == "text.delta"
        or event_type == "text"
        or event_type.endswith(".chunk")
    ):
        return SessionEventKind.TEXT, {"text": data.get("text", "")}

    # Tool stage — the executor uses `tool.call` / `tool.invoke` /
    # `tool.result` / `tool.error`. Map both halves so ToolCallCard
    # can render the request-response pair.
    if "tool" in event_type and ("result" in event_type or "complete" in event_type or "error" in event_type):
        return SessionEventKind.TOOL_RESULT, data
    if "tool" in event_type and ("call" in event_type or "invoke" in event_type or "start" in event_type):
        return SessionEventKind.TOOL_CALL, data

    return None, {}


@dataclass
class SessionRegistry:
    """Stateless-ish lookup by session_id. Lock guards the dict; each
    runtime owns its own per-session lock."""

    _entries: dict[str, SessionRuntime] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def register(self, runtime: SessionRuntime) -> None:
        async with self._lock:
            self._entries[runtime.session_id] = runtime

    async def get(self, session_id: str) -> SessionRuntime:
        async with self._lock:
            try:
                return self._entries[session_id]
            except KeyError as exc:
                raise SessionNotFound(session_id) from exc

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

    async def aclose(self) -> None:
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
