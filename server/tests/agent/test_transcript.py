"""Phase I.4 — event-stream → Transcript grouping + markdown render."""

from __future__ import annotations

from datetime import datetime

from gapt_server.agent.transcript import (
    build_transcript,
    render_markdown,
    to_anthropic_messages,
    to_dict,
)


def _evs(*entries: tuple[str, dict]) -> list[dict]:
    """Build a synthetic session_events list. Auto-fills seq + ts."""
    return [
        {"seq": i, "kind": k, "data": d, "ts": f"2026-06-01T00:00:{i:02d}+00:00"}
        for i, (k, d) in enumerate(entries)
    ]


def test_single_turn_with_user_assistant_and_cost() -> None:
    events = _evs(
        ("user_message", {"text": "what is 1+1?"}),
        ("text", {"text": "2"}),
        ("cost", {"cost_usd": 0.0015, "input_tokens": 8, "output_tokens": 2}),
        ("done", {"cost": {"cost_usd": 0.0015, "input_tokens": 8, "output_tokens": 2}}),
    )
    t = build_transcript(session_id="s1", events=events)
    assert len(t.turns) == 1
    turn = t.turns[0]
    assert turn.user == "what is 1+1?"
    assert turn.assistant == "2"
    assert turn.cost_usd == 0.0015
    assert t.total_cost_usd == 0.0015
    assert t.total_input_tokens == 8


def test_streaming_text_deltas_concatenate() -> None:
    """text frames come in chunks — they must concatenate into a
    single per-turn assistant string."""
    events = _evs(
        ("user_message", {"text": "story"}),
        ("text", {"text": "Once "}),
        ("text", {"text": "upon "}),
        ("text", {"text": "a time"}),
        ("done", {"cost": {"cost_usd": 0.01, "input_tokens": 5, "output_tokens": 10}}),
    )
    t = build_transcript(session_id="s2", events=events)
    assert t.turns[0].assistant == "Once upon a time"


def test_multiple_turns_split_by_user_message() -> None:
    events = _evs(
        ("user_message", {"text": "Q1"}),
        ("text", {"text": "A1"}),
        ("cost", {"cost_usd": 0.001, "input_tokens": 5, "output_tokens": 5}),
        ("user_message", {"text": "Q2"}),
        ("text", {"text": "A2"}),
        ("cost", {"cost_usd": 0.003, "input_tokens": 12, "output_tokens": 15}),
    )
    t = build_transcript(session_id="s3", events=events)
    assert [tn.user for tn in t.turns] == ["Q1", "Q2"]
    assert [tn.assistant for tn in t.turns] == ["A1", "A2"]
    # Per-turn cost is the snapshot delta — turn 1 = 0.001, turn 2 = 0.002.
    assert t.turns[0].cost_usd == 0.001
    assert t.turns[1].cost_usd == 0.002


def test_tool_call_pairs_with_tool_result() -> None:
    events = _evs(
        ("user_message", {"text": "list files"}),
        (
            "tool_call",
            {
                "tool": "bash",
                "tool_use_id": "tu_1",
                "input": {"command": "ls"},
            },
        ),
        (
            "tool_result",
            {
                "tool_use_id": "tu_1",
                "output": "file1\nfile2",
                "is_error": False,
            },
        ),
        ("text", {"text": "There are 2 files."}),
        ("done", {"cost": {"cost_usd": 0.005, "input_tokens": 50, "output_tokens": 30}}),
    )
    t = build_transcript(session_id="s4", events=events)
    turn = t.turns[0]
    assert len(turn.tool_uses) == 1
    tu = turn.tool_uses[0]
    assert tu.tool == "bash"
    assert tu.input == {"command": "ls"}
    assert tu.output == "file1\nfile2"
    assert tu.is_error is False


def test_tool_error_flagged() -> None:
    events = _evs(
        ("user_message", {"text": "run thing"}),
        ("tool_call", {"tool": "bash", "tool_use_id": "tu_e", "input": {}}),
        (
            "tool_result",
            {"tool_use_id": "tu_e", "output": "permission denied", "is_error": True},
        ),
    )
    t = build_transcript(session_id="s5", events=events)
    assert t.turns[0].tool_uses[0].is_error is True


def test_legacy_session_without_user_message() -> None:
    """Pre-Phase-I.2 sessions have no user_message events — the
    grouper must still produce a turn so the transcript isn't blank."""
    events = _evs(
        ("text", {"text": "hello"}),
        ("done", {"cost": {"cost_usd": 0.0002, "input_tokens": 3, "output_tokens": 3}}),
    )
    t = build_transcript(session_id="s6", events=events)
    assert len(t.turns) == 1
    assert t.turns[0].user == ""
    assert t.turns[0].assistant == "hello"


def test_markdown_renders_user_assistant_and_tool() -> None:
    events = _evs(
        ("user_message", {"text": "what's in cwd?"}),
        (
            "tool_call",
            {"tool": "bash", "tool_use_id": "tu_x", "input": {"command": "pwd"}},
        ),
        (
            "tool_result",
            {"tool_use_id": "tu_x", "output": "/workspace", "is_error": False},
        ),
        ("text", {"text": "You're in /workspace."}),
        ("done", {"cost": {"cost_usd": 0.0004, "input_tokens": 12, "output_tokens": 8}}),
    )
    t = build_transcript(session_id="s7", events=events)
    md = render_markdown(t)
    assert "# Session s7" in md
    assert "## Turn 1" in md
    assert "**User**" in md
    assert "what's in cwd?" in md
    assert "**Assistant**" in md
    assert "You're in /workspace." in md
    assert "`bash`" in md
    assert "/workspace" in md


def test_to_dict_is_json_friendly() -> None:
    """`to_dict` output must be plain dicts/lists/primitives so it
    round-trips through json.dumps without custom encoders."""
    import json

    events = _evs(
        ("user_message", {"text": "hi"}),
        ("text", {"text": "hello"}),
        ("done", {"cost": {"cost_usd": 0.001, "input_tokens": 2, "output_tokens": 1}}),
    )
    t = build_transcript(session_id="s8", events=events)
    payload = to_dict(t)
    # Must not raise:
    json.dumps(payload)
    assert payload["session_id"] == "s8"
    assert payload["turns"][0]["user"] == "hi"


def test_cache_tokens_surfaced_in_totals() -> None:
    """Phase K.2 — the transcript builder must propagate cache token
    counts from the latest `cost` (or `done`) snapshot into the
    Transcript totals so the SessionDetail header can render them."""
    events = _evs(
        ("user_message", {"text": "prime the cache"}),
        (
            "cost",
            {
                "cost_usd": 0.013,
                "input_tokens": 6,
                "output_tokens": 6,
                "cache_write_tokens": 3400,
                "cache_read_tokens": 200,
            },
        ),
        (
            "done",
            {
                "cost": {
                    "cost_usd": 0.013,
                    "input_tokens": 6,
                    "output_tokens": 6,
                    "cache_write_tokens": 3400,
                    "cache_read_tokens": 200,
                }
            },
        ),
    )
    t = build_transcript(session_id="sk", events=events)
    assert t.total_cache_write_tokens == 3400
    assert t.total_cache_read_tokens == 200
    payload = to_dict(t)
    assert payload["total_cache_write_tokens"] == 3400
    assert payload["total_cache_read_tokens"] == 200
    md = render_markdown(t)
    assert "cache_write tokens: 3,400" in md
    assert "cache_read tokens: 200" in md


def test_to_anthropic_messages_flat_user_assistant_pairs() -> None:
    """Phase L.1 — the rehydrate path turns a transcript into the
    Anthropic `messages` array so `PipelineState.messages` carries
    the prior conversation forward."""
    events = _evs(
        ("user_message", {"text": "내 이름은 alice"}),
        ("text", {"text": "안녕 alice!"}),
        ("done", {"cost": {"cost_usd": 0.001, "input_tokens": 5, "output_tokens": 5}}),
        ("user_message", {"text": "내 이름이 뭐였지?"}),
        ("text", {"text": "alice 라고 했어요"}),
        ("done", {"cost": {"cost_usd": 0.002, "input_tokens": 10, "output_tokens": 8}}),
    )
    transcript = build_transcript(session_id="s_mem", events=events)
    msgs = to_anthropic_messages(transcript)
    assert msgs == [
        {"role": "user", "content": "내 이름은 alice"},
        {"role": "assistant", "content": "안녕 alice!"},
        {"role": "user", "content": "내 이름이 뭐였지?"},
        {"role": "assistant", "content": "alice 라고 했어요"},
    ]


def test_to_anthropic_messages_skips_empty_turns() -> None:
    """Legacy pre-Phase-I.2 sessions have no `user_message` events —
    the resulting Turn has user="" + assistant="something". Skip these
    so the API doesn't choke on an empty-content user message."""
    events = _evs(
        # No user_message event before this — legacy assistant-only turn.
        ("text", {"text": "legacy reply"}),
        ("done", {"cost": {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}}),
        # A proper turn afterwards must still show up.
        ("user_message", {"text": "hi"}),
        ("text", {"text": "hello"}),
    )
    transcript = build_transcript(session_id="s_legacy", events=events)
    msgs = to_anthropic_messages(transcript)
    assert msgs == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_to_anthropic_messages_max_turns_cap() -> None:
    """Long sessions risk blowing the model context window the moment
    they're rehydrated. The cap keeps only the last N turns."""
    events_list: list = []
    for i in range(10):
        events_list.append(("user_message", {"text": f"q{i}"}))
        events_list.append(("text", {"text": f"a{i}"}))
    transcript = build_transcript(session_id="s_long", events=_evs(*events_list))
    msgs = to_anthropic_messages(transcript, max_turns=3)
    # 3 turns × 2 messages/turn = 6 messages.
    assert len(msgs) == 6
    # The latest three turns are preserved.
    assert msgs[0]["content"] == "q7"
    assert msgs[-1]["content"] == "a9"


def test_datetime_started_at_parsed_from_iso() -> None:
    """`started_at` should be a real datetime so the markdown render
    can call `.isoformat()` directly. The event grouper parses the
    string timestamps from session_events rows."""
    events = _evs(("user_message", {"text": "hi"}))
    t = build_transcript(session_id="s9", events=events)
    assert isinstance(t.turns[0].started_at, datetime)


# ────────────────────────────────── Phase M.7 — tool-call rehydration ──


def test_to_anthropic_messages_includes_tool_use_and_tool_result_blocks() -> None:
    """Phase M.7 — when a turn ran tools, the rebuilt messages must
    carry both the assistant's tool_use block AND the matching
    user-side tool_result block so the agent's memory of the call +
    its output survives across rehydrate. Pre-M.7 the rebuilt array
    only had natural-language text; the agent saw "delete file X" →
    "did you delete it?" with no record of having ran rm, and
    re-issued the deletion blindly."""
    events = _evs(
        ("user_message", {"text": "remove README.md"}),
        ("tool_call", {
            "tool_name": "Bash",
            "tool_use_id": "toolu_01",
            "input": {"command": "rm README.md"},
        }),
        ("tool_result", {
            "tool_use_id": "toolu_01",
            "is_error": False,
            "content": "removed",
        }),
        ("text", {"text": "Removed README.md."}),
        ("done", {"cost": {"cost_usd": 0.001, "input_tokens": 5, "output_tokens": 5}}),
    )
    transcript = build_transcript(session_id="s_tool", events=events)
    msgs = to_anthropic_messages(transcript)

    # Expected shape: user → assistant(tool_use) → user(tool_result) → assistant(text)
    assert len(msgs) == 4
    assert msgs[0] == {"role": "user", "content": "remove README.md"}

    assistant_tools = msgs[1]
    assert assistant_tools["role"] == "assistant"
    assert isinstance(assistant_tools["content"], list)
    assert assistant_tools["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_01",
            "name": "Bash",
            "input": {"command": "rm README.md"},
        }
    ]

    user_results = msgs[2]
    assert user_results["role"] == "user"
    assert isinstance(user_results["content"], list)
    assert user_results["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_01",
            "content": "removed",
            "is_error": False,
        }
    ]

    assert msgs[3] == {"role": "assistant", "content": "Removed README.md."}


def test_to_anthropic_messages_tool_blocks_can_be_disabled() -> None:
    """`include_tool_blocks=False` falls back to the L.1 text-only
    shape — useful for tests that mock pipelines without a tool layer."""
    events = _evs(
        ("user_message", {"text": "run X"}),
        ("tool_call", {
            "tool_name": "Bash",
            "tool_use_id": "toolu_02",
            "input": {"command": "X"},
        }),
        ("tool_result", {"tool_use_id": "toolu_02", "is_error": False, "content": "ok"}),
        ("text", {"text": "Done."}),
    )
    transcript = build_transcript(session_id="s_disable", events=events)
    msgs = to_anthropic_messages(transcript, include_tool_blocks=False)
    assert msgs == [
        {"role": "user", "content": "run X"},
        {"role": "assistant", "content": "Done."},
    ]


def test_to_anthropic_messages_skips_tools_missing_use_id() -> None:
    """Legacy tool events without `tool_use_id` would break the
    Anthropic API (tool_use blocks require an id). Drop them rather
    than poison the rebuilt messages — the assistant text still rides."""
    events = _evs(
        ("user_message", {"text": "do thing"}),
        ("tool_call", {"tool_name": "Bash", "input": {"command": "x"}}),  # no id
        ("text", {"text": "ok"}),
    )
    transcript = build_transcript(session_id="s_legacy_tool", events=events)
    msgs = to_anthropic_messages(transcript)
    # Fell through to the L.1 plain shape since the only tool lacked an id.
    assert msgs == [
        {"role": "user", "content": "do thing"},
        {"role": "assistant", "content": "ok"},
    ]


def test_to_anthropic_messages_multi_tool_round_trip() -> None:
    """Multiple tools in one turn surface as a single grouped
    assistant→user pair (matching the Anthropic API expectation of
    all tool_uses in one msg + all tool_results in the following one)."""
    events = _evs(
        ("user_message", {"text": "list and read"}),
        ("tool_call", {
            "tool_name": "Bash",
            "tool_use_id": "t1",
            "input": {"command": "ls"},
        }),
        ("tool_result", {"tool_use_id": "t1", "is_error": False, "content": "file.txt"}),
        ("tool_call", {
            "tool_name": "Read",
            "tool_use_id": "t2",
            "input": {"path": "file.txt"},
        }),
        ("tool_result", {"tool_use_id": "t2", "is_error": False, "content": "hello"}),
        ("text", {"text": "Found file.txt with 'hello'."}),
    )
    transcript = build_transcript(session_id="s_multi", events=events)
    msgs = to_anthropic_messages(transcript)
    assert len(msgs) == 4

    assistant_tools = msgs[1]["content"]
    assert [b["id"] for b in assistant_tools] == ["t1", "t2"]
    assert [b["name"] for b in assistant_tools] == ["Bash", "Read"]

    user_results = msgs[2]["content"]
    assert [b["tool_use_id"] for b in user_results] == ["t1", "t2"]
    assert [b["content"] for b in user_results] == ["file.txt", "hello"]
