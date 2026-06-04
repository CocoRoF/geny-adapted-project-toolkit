"""Phase I.4 — session_events → user-readable transcript.

Groups the raw event stream into *turns* keyed by `user_message`
events: every user_message starts a new turn, and the subsequent
`text` / `tool_call` / `tool_result` / `cost` frames attach to that
turn until the next user_message (or DONE) closes it.

Two output formats:
- ``json``: programmatic — `{session_id, turns: [{user, assistant,
  tool_uses: [...], cost_usd}]}` — for downstream archiving tools.
- ``markdown``: human review — `### Turn N\\n**User**: …\\n**Assistant**:
  …\\n#### Tool: bash\\n…` — for the "vibe-coding archive" the
  operator downloads from the chat panel.

The grouper is forgiving about ordering anomalies — duplicate
tool_results, missing user_messages at the head of the events
(pre-Phase-I sessions), interleaved cost frames. The goal is "render
*something* useful" rather than "fail if the stream is weird"."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ToolUse:
    """One `tool_call` + its matching `tool_result`. The result may
    be `None` if the session ended before the tool returned (cancelled,
    crashed, still running)."""

    tool: str
    tool_use_id: str | None
    input: Any = None
    output: Any = None
    is_error: bool = False


@dataclass
class Turn:
    """One round of conversation. `user` is empty for legacy turns
    captured before Phase I.2 (no user_message event). `assistant`
    is the concatenated text deltas. `cost_usd` is the *delta* across
    this turn — extracted from the closing `cost` event when present."""

    user: str = ""
    assistant: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    cost_usd: float = 0.0
    started_at: datetime | None = None


@dataclass
class Transcript:
    session_id: str
    turns: list[Turn]
    # Cumulative totals — useful when the consumer just wants the
    # session-level numbers without re-summing.
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    # Phase K.2 — Anthropic cache token totals so the SessionDetail
    # header can explain "$0.013 for 6 tokens" (because 3.4k tokens
    # were primed into the cache that turn).
    total_cache_write_tokens: int = 0
    total_cache_read_tokens: int = 0


def build_transcript(
    *,
    session_id: str,
    events: list[dict[str, Any]],
) -> Transcript:
    """Group an ordered list of `{kind, data, ts, seq}` event dicts
    into Turn objects. The input is whatever `session_events` rows
    look like after `.to_dict()`-ish marshalling."""
    turns: list[Turn] = []
    current: Turn | None = None
    # Index tool_use_id → ToolUse so a later tool_result can attach.
    pending_tools: dict[str, ToolUse] = {}
    # Accumulator-snapshot tracking. Each cost event carries the
    # session-level rolling totals; per-turn cost = (latest snapshot
    # for this turn) − (snapshot at the start of this turn).
    last_cost_snapshot: dict[str, float] = {
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        # Phase K.2 — same delta-tracking semantics as the input/output
        # counts above; carried separately because the SessionDetail
        # header surfaces them as their own line.
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
    }
    turn_start_cost: dict[str, float] = dict(last_cost_snapshot)

    def _open_turn(text: str, ts: datetime | None) -> Turn:
        nonlocal current
        # Roll the prior turn's tool-use cursor.
        pending_tools.clear()
        current = Turn(user=text, started_at=ts)
        turns.append(current)
        # Anchor the start-of-turn cost snapshot to whatever the
        # accumulator last reported.
        turn_start_cost["cost_usd"] = last_cost_snapshot["cost_usd"]
        turn_start_cost["input_tokens"] = last_cost_snapshot["input_tokens"]
        turn_start_cost["output_tokens"] = last_cost_snapshot["output_tokens"]
        return current

    for ev in events:
        kind = ev.get("kind", "")
        data = ev.get("data") or {}
        ts_raw = ev.get("ts")
        ts: datetime | None = None
        if isinstance(ts_raw, str):
            # ISO timestamps from the JSON serialiser.
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                ts = None
        elif isinstance(ts_raw, datetime):
            ts = ts_raw

        if kind == "user_message":
            _open_turn(str(data.get("text", "")), ts)
            continue

        # Legacy: pre-Phase-I.2 sessions have no user_message frames.
        # Seed an empty turn the first time anything assistant-y shows
        # up so the rest of the rows have a place to land.
        if current is None and kind in {
            "text",
            "tool_call",
            "tool_result",
            "cost",
            "done",
        }:
            _open_turn("", ts)

        if kind == "text":
            chunk = str(data.get("text", ""))
            if current is not None:
                current.assistant += chunk
        elif kind == "tool_call":
            tool_name = str(data.get("tool") or data.get("tool_name") or "")
            tu_id_raw = data.get("tool_use_id")
            tu_id = str(tu_id_raw) if tu_id_raw is not None else None
            tool_use = ToolUse(
                tool=tool_name,
                tool_use_id=tu_id,
                input=data.get("input"),
            )
            if current is not None:
                current.tool_uses.append(tool_use)
            if tu_id is not None:
                pending_tools[tu_id] = tool_use
        elif kind == "tool_result":
            tu_id_raw = data.get("tool_use_id")
            tu_id = str(tu_id_raw) if tu_id_raw is not None else None
            target = pending_tools.get(tu_id) if tu_id is not None else None
            if target is None:
                # Stray result — attach as a synthetic ToolUse so the
                # transcript still shows something.
                target = ToolUse(tool="", tool_use_id=tu_id)
                if current is not None:
                    current.tool_uses.append(target)
            target.output = data.get("output", data.get("content"))
            target.is_error = bool(data.get("is_error"))
        elif kind == "cost":
            # Snapshot semantics — see _open_turn above. We update the
            # running total each time and recompute the *current* turn's
            # delta off the snapshot we anchored at open.
            new_cost = float(data.get("cost_usd", 0.0) or 0.0)
            new_in = int(data.get("input_tokens", 0) or 0)
            new_out = int(data.get("output_tokens", 0) or 0)
            new_cw = int(data.get("cache_write_tokens", 0) or 0)
            new_cr = int(data.get("cache_read_tokens", 0) or 0)
            if current is not None:
                current.cost_usd = new_cost - turn_start_cost["cost_usd"]
            last_cost_snapshot["cost_usd"] = new_cost
            last_cost_snapshot["input_tokens"] = new_in
            last_cost_snapshot["output_tokens"] = new_out
            last_cost_snapshot["cache_write_tokens"] = new_cw
            last_cost_snapshot["cache_read_tokens"] = new_cr
        elif kind == "done":
            # The DONE frame carries the final accumulator snapshot;
            # use it to close out the turn's cost if no `cost` events
            # arrived (some adapters skip cost mid-flight).
            snap = data.get("cost") if isinstance(data, dict) else None
            if isinstance(snap, dict) and current is not None and current.cost_usd == 0.0:
                final_cost = float(snap.get("cost_usd", 0.0) or 0.0)
                current.cost_usd = final_cost - turn_start_cost["cost_usd"]
                last_cost_snapshot["cost_usd"] = final_cost
                last_cost_snapshot["input_tokens"] = int(
                    snap.get("input_tokens", last_cost_snapshot["input_tokens"]) or 0
                )
                last_cost_snapshot["output_tokens"] = int(
                    snap.get("output_tokens", last_cost_snapshot["output_tokens"]) or 0
                )
                last_cost_snapshot["cache_write_tokens"] = int(
                    snap.get(
                        "cache_write_tokens",
                        last_cost_snapshot["cache_write_tokens"],
                    )
                    or 0
                )
                last_cost_snapshot["cache_read_tokens"] = int(
                    snap.get(
                        "cache_read_tokens",
                        last_cost_snapshot["cache_read_tokens"],
                    )
                    or 0
                )

    return Transcript(
        session_id=session_id,
        turns=turns,
        total_cost_usd=last_cost_snapshot["cost_usd"],
        total_input_tokens=int(last_cost_snapshot["input_tokens"]),
        total_output_tokens=int(last_cost_snapshot["output_tokens"]),
        total_cache_write_tokens=int(last_cost_snapshot["cache_write_tokens"]),
        total_cache_read_tokens=int(last_cost_snapshot["cache_read_tokens"]),
    )


def render_markdown(transcript: Transcript) -> str:
    """Operator-facing markdown — what the chat panel downloads as
    a `.md` file. Format intentionally minimal so it pastes cleanly
    into a wiki / notebook without escaping headaches."""
    lines: list[str] = [
        f"# Session {transcript.session_id}",
        "",
        f"- Total cost: ${transcript.total_cost_usd:.6f}",
        f"- Total input tokens: {transcript.total_input_tokens:,}",
        f"- Total output tokens: {transcript.total_output_tokens:,}",
    ]
    if transcript.total_cache_write_tokens:
        lines.append(
            f"- Total cache_write tokens: {transcript.total_cache_write_tokens:,}"
        )
    if transcript.total_cache_read_tokens:
        lines.append(
            f"- Total cache_read tokens: {transcript.total_cache_read_tokens:,}"
        )
    lines.append(f"- Turns: {len(transcript.turns)}")
    lines.append("")
    for idx, turn in enumerate(transcript.turns, start=1):
        lines.append(f"## Turn {idx}")
        if turn.started_at is not None:
            lines.append(f"*{turn.started_at.isoformat()}*")
        lines.append("")
        if turn.user:
            lines.append("**User**")
            lines.append("")
            lines.append(_indent_quote(turn.user))
            lines.append("")
        if turn.assistant:
            lines.append("**Assistant**")
            lines.append("")
            lines.append(turn.assistant.rstrip())
            lines.append("")
        for tu in turn.tool_uses:
            lines.append(f"### 🔧 Tool: `{tu.tool}`")
            if tu.input is not None:
                lines.append("")
                lines.append("Input:")
                lines.append("```json")
                lines.append(json.dumps(tu.input, ensure_ascii=False, indent=2))
                lines.append("```")
            if tu.output is not None:
                lines.append("")
                lines.append("Output" + (" (error)" if tu.is_error else "") + ":")
                lines.append("```")
                # Truncate large outputs so the file doesn't balloon.
                rendered = (
                    tu.output
                    if isinstance(tu.output, str)
                    else json.dumps(tu.output, ensure_ascii=False, indent=2)
                )
                if len(rendered) > 4000:
                    rendered = rendered[:4000] + "\n…(truncated)"
                lines.append(rendered)
                lines.append("```")
            lines.append("")
        if turn.cost_usd:
            lines.append(f"_Turn cost: ${turn.cost_usd:.6f}_")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _indent_quote(text: str) -> str:
    """Render `text` as a `>`-prefixed markdown blockquote. Each line
    is prefixed so multi-line user prompts stay grouped visually."""
    return "\n".join(f"> {line}" for line in text.splitlines() or [""])


def to_anthropic_messages(
    transcript: Transcript,
    *,
    max_turns: int | None = 50,
    include_tool_blocks: bool = True,
) -> list[dict[str, Any]]:
    """Convert a transcript into the Anthropic `messages` array format
    so a freshly-rehydrated `PipelineState` can seed prior conversation.

    Format: `[{"role": "user", "content": "..."}, {"role": "assistant",
    "content": "..."}, ...]` — exactly what `state.messages` carries.

    Phase L.1 — used by the session rehydrate path. Skips turns whose
    user OR assistant text is empty (legacy pre-Phase-I.2 sessions
    where the prompt was never persisted; pre-rendering an empty
    user-side message confuses the API and the assistant downstream).

    `max_turns` caps the prefix to the most recent N turns so a long-
    running session doesn't blow past the model's context window the
    moment it gets rehydrated. None = no cap (test-only — production
    paths should always cap).

    Phase M.7 — when `include_tool_blocks` is true (default), turns
    that ran tools are rebuilt with proper Anthropic content blocks:
    ``assistant: [tool_use, ...]`` followed by ``user: [tool_result,
    ...]`` then a final ``assistant: text`` with the post-tools
    response. This preserves the agent's memory of which tools it
    called and what they returned across rehydrate boundaries, so
    "delete file X" → "did you delete it?" works after a server
    restart instead of the agent re-running the tool blindly. Setting
    `include_tool_blocks=False` falls back to the L.1 text-only shape
    for tests that don't want to mock the tool layer.

    Format per turn with tools:
    - ``{role:user, content:user_text}``
    - ``{role:assistant, content:[{type:tool_use, id, name, input}, ...]}``
    - ``{role:user, content:[{type:tool_result, tool_use_id, content, is_error}, ...]}``
    - ``{role:assistant, content:assistant_text}`` (post-tools natural reply)

    For turns without tools, falls through to the L.1 plain shape:
    - ``{role:user, content:user_text}``
    - ``{role:assistant, content:assistant_text}``
    """
    turns = transcript.turns
    if max_turns is not None and len(turns) > max_turns:
        turns = turns[-max_turns:]
    out: list[dict[str, Any]] = []
    for turn in turns:
        user_text = (turn.user or "").strip()
        assistant_text = (turn.assistant or "").strip()
        if not user_text or not assistant_text:
            continue
        out.append({"role": "user", "content": user_text})

        # Phase M.7 — when the turn ran tools and tool blocks are
        # enabled, surface them as proper content blocks. Skip tools
        # whose `tool_use_id` is missing (legacy pre-Phase-D events) —
        # Anthropic rejects tool_result/tool_use blocks without an id.
        usable_tools = (
            [tu for tu in turn.tool_uses if tu.tool_use_id]
            if include_tool_blocks
            else []
        )
        if usable_tools:
            tool_use_blocks = [
                {
                    "type": "tool_use",
                    "id": tu.tool_use_id,
                    "name": tu.tool or "tool",
                    "input": tu.input if isinstance(tu.input, dict) else {},
                }
                for tu in usable_tools
            ]
            tool_result_blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": tu.tool_use_id,
                    # `content` accepts string OR a content-block list
                    # per Anthropic API. We stringify object outputs
                    # to keep the surface predictable — the agent
                    # mostly cares about whether the call succeeded
                    # and what shape it returned, not exact JSON.
                    "content": _stringify_tool_output(tu.output),
                    "is_error": tu.is_error,
                }
                for tu in usable_tools
            ]
            out.append({"role": "assistant", "content": tool_use_blocks})
            out.append({"role": "user", "content": tool_result_blocks})

        out.append({"role": "assistant", "content": assistant_text})
    return out


def _stringify_tool_output(output: Any) -> str:
    """Coerce a tool's `output` field into a string for the
    `tool_result.content` slot. Strings pass through; dict/list go
    through `json.dumps`. None → empty. Cap at 8 KB so a misbehaving
    tool that returned a huge log doesn't push the model's context."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output[:8192]
    try:
        return json.dumps(output, ensure_ascii=False, default=str)[:8192]
    except (TypeError, ValueError):
        return str(output)[:8192]


def to_dict(transcript: Transcript) -> dict[str, Any]:
    """JSON-friendly dump for the `?format=json` response."""
    return {
        "session_id": transcript.session_id,
        "total_cost_usd": round(transcript.total_cost_usd, 6),
        "total_input_tokens": transcript.total_input_tokens,
        "total_output_tokens": transcript.total_output_tokens,
        "total_cache_write_tokens": transcript.total_cache_write_tokens,
        "total_cache_read_tokens": transcript.total_cache_read_tokens,
        "turns": [
            {
                "user": t.user,
                "assistant": t.assistant,
                "cost_usd": round(t.cost_usd, 6),
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "tool_uses": [
                    {
                        "tool": tu.tool,
                        "tool_use_id": tu.tool_use_id,
                        "input": tu.input,
                        "output": tu.output,
                        "is_error": tu.is_error,
                    }
                    for tu in t.tool_uses
                ],
            }
            for t in transcript.turns
        ],
    }
