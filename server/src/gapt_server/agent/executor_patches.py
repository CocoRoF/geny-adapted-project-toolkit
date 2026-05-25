"""Runtime patches on top of `geny-executor`.

Two reasons this module exists:

1. **Agentic visibility** — the executor's s06_api default-artifact
   `_call_streaming` forwards *only* `text_delta` chunks to
   `state.add_event`. Other canonical chunks the provider emits —
   `tool_use` / `thinking_delta` / `input_json_delta` /
   `content_block_stop` — get silently dropped between the provider
   stream and the pipeline event bus. Without our patched
   `_call_streaming`, the GAPT chat trace has no idea any tool ran.

2. **Workspace sandbox routing** — the bundled
   `CLIProcessRunner._spawn` runs the `claude` binary directly on the
   host. That means `cd /` from inside the agent's view exposes the
   GAPT operator's `/home`, sibling worktrees, and every other host
   path the server process can read. We swap `_spawn` for a wrapper
   that checks a ContextVar set by the session invoke runner: when a
   sandbox is bound for the current task we re-route the call through
   ``docker exec -i <gapt-ws-X> claude ...`` so the agent only ever
   sees the workspace's bind-mounted `/workspace`. ContextVar
   propagation gives us per-task scoping for free — two concurrent
   sessions for two different workspaces stay isolated even though
   they share the patched function.

Per [[feedback_extend_executor_not_adapter_layer]] the longer-term
fix for #1 belongs upstream. #2 is a GAPT-server concern (the
executor library has no opinion on per-tenant containment), so it
stays here.

Applied once at import time of `gapt_server.agent` (see __init__).
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import sys
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from geny_executor.llm_client._cli_runtime import CLIProcessRunner
from geny_executor.llm_client.base import BaseClient
from geny_executor.llm_client.translators._cli import StreamJsonAccumulator
from geny_executor.stages.s06_api.artifact.default.stage import (
    APIError,
    APIStage,
    ErrorCategory,
    ExecutorErrorCode,
    PipelineState,
)

if TYPE_CHECKING:
    from gapt_server.domains.workspace_sandbox import WorkspaceSandbox


# Bound by the session-invoke runner just before `pipeline.run_stream`.
# When set, the patched _spawn re-routes the CLI call through this
# sandbox's container. ContextVar (not module global) so two concurrent
# session invocations in the same process can target different
# containers.
_CURRENT_SANDBOX: contextvars.ContextVar[WorkspaceSandbox | None] = (
    contextvars.ContextVar("gapt_current_sandbox", default=None)
)


def set_current_sandbox(sandbox: WorkspaceSandbox | None) -> contextvars.Token:
    """Set the sandbox the current async task should route CLI spawns
    into. Returns the token the caller should pass to
    `reset_current_sandbox` in a `finally` block."""
    return _CURRENT_SANDBOX.set(sandbox)


def reset_current_sandbox(token: contextvars.Token) -> None:
    _CURRENT_SANDBOX.reset(token)


_ORIGINAL_FEED = StreamJsonAccumulator.feed
_ORIGINAL_SPAWN = CLIProcessRunner._spawn  # type: ignore[attr-defined]


def _patched_feed(self: StreamJsonAccumulator, line: dict[str, Any]) -> list[dict[str, Any]]:
    """Wraps the accumulator's `feed` so that `user` lines carrying
    `tool_result` content blocks surface as `{"type":"tool_result",
    "tool_use_id":..., "content":...}` events. The upstream `feed`
    drops every `user` line as "echo of our input" — true for the
    *original* user prompt, but false for the synthetic user-role
    messages the CLI injects after each tool call to return the
    tool's output to the assistant. Without this the ToolCallCard
    stays in "running" forever even after the assistant's final
    answer lands."""
    if isinstance(line, dict) and str(line.get("type", "")) == "user":
        msg = line.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            events: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                # `content` field can be a string or a list of
                # text/image blocks. Flatten to a single string so
                # the UI can render it in one row.
                raw = block.get("content")
                if isinstance(raw, list):
                    parts: list[str] = []
                    for c in raw:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(str(c.get("text", "")))
                        elif isinstance(c, str):
                            parts.append(c)
                    text = "".join(parts)
                elif isinstance(raw, str):
                    text = raw
                else:
                    text = ""
                events.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("tool_use_id"),
                        "is_error": bool(block.get("is_error")),
                        "content": text,
                    }
                )
            if events:
                return events
    return _ORIGINAL_FEED(self, line)


async def _patched_call_streaming(
    self: APIStage,
    client: BaseClient,
    cfg: Any,
    state: PipelineState,
) -> Any:
    """Re-implementation that also forwards `tool_use` chunks.

    Behavioural delta vs upstream:
      * adds `state.add_event("tool.invoke", {tool_use_id, name, input})`
        per accumulated tool_use frame (the provider streams the input
        as a sequence of `input_json_delta` partial-json fragments; we
        join them and emit one event per *complete* tool_use block).
      * adds `state.add_event("thinking.delta", {text: ...})` for the
        thinking content block (lets the UI show "thinking…" beats).

    Everything else is identical — `text_delta` still flows through,
    `message_complete` still wins.
    """
    response: Any | None = None
    kwargs = self._call_kwargs(cfg, state)

    stream: AsyncIterator[dict[str, Any]] = client.create_message_stream(**kwargs)

    # Per-index accumulators for tool_use blocks. Provider may emit
    # `tool_use` with the metadata then a sequence of `input_json_delta`
    # frames whose `partial_json` strings concatenate to the full JSON
    # input. We finalise on the matching `content_block_stop`.
    pending_tool: dict[int, dict[str, Any]] = {}
    partial_input: dict[int, list[str]] = {}

    async for chunk in stream:
        chunk_type = chunk.get("type")
        if chunk_type == "message_complete":
            response = chunk["response"]
            continue
        if chunk_type == "text_delta":
            text = chunk.get("text")
            if text:
                state.add_event("text.delta", {"text": text})
            continue
        if chunk_type == "thinking_delta":
            text = chunk.get("text")
            if text:
                state.add_event("thinking.delta", {"text": text})
            continue
        if chunk_type == "tool_use":
            # Two shapes arrive here:
            #   - Form 1 (true streaming): `input` is empty dict, the
            #     real args come later as `input_json_delta` frames
            #     concatenated until `content_block_stop`. We queue
            #     and flush on stop.
            #   - Form 2 (Claude Code 2.x message form): `input` is the
            #     full dict already. Emit immediately because the
            #     accumulator skips emitting `content_block_stop` for
            #     this form.
            idx = int(chunk.get("index", -1))
            input_payload = chunk.get("input") or {}
            if input_payload:
                state.add_event(
                    "tool.invoke",
                    {
                        "tool_use_id": chunk.get("id"),
                        "name": chunk.get("name"),
                        "input": input_payload,
                    },
                )
            else:
                pending_tool[idx] = {
                    "tool_use_id": chunk.get("id"),
                    "name": chunk.get("name"),
                    "input": input_payload,
                }
                partial_input.setdefault(idx, [])
            continue
        if chunk_type == "input_json_delta":
            idx = int(chunk.get("index", -1))
            # The accumulator's translator output uses `delta` (not
            # `partial_json`) as the JSON-fragment field. The provider
            # may pass either depending on shape — accept both.
            frag = chunk.get("delta") or chunk.get("partial_json", "")
            partial_input.setdefault(idx, []).append(str(frag))
            continue
        if chunk_type == "content_block_stop":
            idx = int(chunk.get("index", -1))
            meta = pending_tool.pop(idx, None)
            if meta is None:
                continue
            joined = "".join(partial_input.pop(idx, [])).strip()
            input_payload = meta["input"]
            if joined:
                try:
                    import json as _json

                    input_payload = _json.loads(joined)
                except Exception:
                    input_payload = {"_raw": joined}
            state.add_event(
                "tool.invoke",
                {
                    "tool_use_id": meta["tool_use_id"],
                    "name": meta["name"],
                    "input": input_payload,
                },
            )
            continue
        if chunk_type == "tool_result":
            # Synthesised by `_patched_feed` for `user` lines that
            # carry tool_result blocks (CLI-internal tool completions).
            state.add_event(
                "tool.result",
                {
                    "tool_use_id": chunk.get("tool_use_id"),
                    "is_error": chunk.get("is_error", False),
                    "content": chunk.get("content", ""),
                },
            )
            continue
        # Unknown chunk types: keep silent. The upstream method does
        # the same; we don't want to drown the event bus.

    if response is None:
        raise APIError(
            "Stream ended without message_complete",
            category=ErrorCategory.UNKNOWN,
            code=ExecutorErrorCode.EXEC_API_STREAM_INCOMPLETE,
        )
    return response


async def _patched_spawn(
    self: CLIProcessRunner, argv: Any
) -> tuple[asyncio.subprocess.Process, float]:
    """Wraps `CLIProcessRunner._spawn`.

    - With no sandbox bound to the current task → behaves identically
      to the upstream `_spawn` (test paths, oneshot host invocations).
    - With a sandbox bound → rewrites the spawn so it runs inside that
      sandbox's docker container:
        * argv becomes `docker exec -i -w /workspace --env ... <container>
          claude <original argv>`
        * cwd on the host side is unused (the container's `-w` sets the
          child's cwd inside the container)
        * env_extras flow through as `--env KEY=VAL` flags so the
          ANTHROPIC_API_KEY etc. land inside the container instead of
          the host process

    `start_new_session=True` is preserved on POSIX so the existing
    timeout / cancellation path can still killpg the docker exec
    process group; sending SIGTERM to the host docker exec propagates
    to the child claude process inside the container.

    Why not configure this through `creds.extras`: the executor's
    `_creds_to_client_kwargs` only forwards a fixed allowlist of
    extras to the client (`workspace_dir`, `bare_mode`, ...). Adding a
    new dimension there means patching the executor itself. The
    ContextVar lets us scope per-invocation without changing the
    library's surface."""
    sandbox = _CURRENT_SANDBOX.get()
    if sandbox is None:
        return await _ORIGINAL_SPAWN(self, argv)

    # Sandbox might not have a container running yet — first agent
    # call after a server restart. ensure() is idempotent.
    try:
        await sandbox.ensure()
    except Exception:
        pass

    docker_argv: list[str] = ["exec", "-i", "-w", "/workspace"]
    env_extras = dict(self.env_extras or {})
    for k, v in env_extras.items():
        docker_argv += ["--env", f"{k}={v}"]
    # Inside the container the agent CLI is always `claude` on PATH
    # (the gapt-workspace image installs it via npm). We deliberately
    # don't forward `self.binary` (a host-side path that doesn't
    # exist in the container).
    docker_argv += [sandbox.container_name, "claude", *list(argv)]

    kwargs: dict[str, Any] = dict(
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Docker binary needs the *host* env (PATH, DOCKER_HOST,
        # ...). The child's view of env is what we passed via --env
        # flags above; that's separate.
        env=os.environ.copy(),
        cwd=None,
    )
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec("docker", *docker_argv, **kwargs)
    return proc, time.monotonic()


_APPLIED = False


def apply_executor_patches() -> None:
    """Install the streaming + sandbox patches. Idempotent."""
    global _APPLIED  # noqa: PLW0603
    if _APPLIED:
        return
    APIStage._call_streaming = _patched_call_streaming  # type: ignore[assignment]
    StreamJsonAccumulator.feed = _patched_feed  # type: ignore[assignment]
    CLIProcessRunner._spawn = _patched_spawn  # type: ignore[assignment]
    _APPLIED = True
