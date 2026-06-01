"""Phase I.4 — event-stream → Transcript grouping + markdown render."""

from __future__ import annotations

from datetime import datetime

from gapt_server.agent.transcript import (
    build_transcript,
    render_markdown,
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


def test_datetime_started_at_parsed_from_iso() -> None:
    """`started_at` should be a real datetime so the markdown render
    can call `.isoformat()` directly. The event grouper parses the
    string timestamps from session_events rows."""
    events = _evs(("user_message", {"text": "hi"}))
    t = build_transcript(session_id="s9", events=events)
    assert isinstance(t.turns[0].started_at, datetime)
