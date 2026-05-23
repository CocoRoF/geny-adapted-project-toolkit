"""Mask known secret patterns from audit payloads before insert."""

from __future__ import annotations

from gapt_server.domains.audit.masking import scrub


def test_anthropic_key_redacted() -> None:
    payload = {"prompt": "use sk-ant-abcdefghijklmnopqrstuvwxyz1234567890 please"}
    assert "[redacted:anthropic_api_key]" in scrub(payload)["prompt"]
    assert "sk-ant-" not in scrub(payload)["prompt"]


def test_openai_key_redacted() -> None:
    assert "[redacted:openai_api_key]" in scrub("token=sk-1234567890abcdefghijklmnop")


def test_github_pat_redacted() -> None:
    assert "[redacted:github_pat]" in scrub("env GH=ghp_1234567890abcdefghij")
    assert "[redacted:github_fine_grained_pat]" in scrub("env GH=github_pat_abc_1234567890abcdef")


def test_bearer_token_redacted() -> None:
    out = scrub("Authorization: Bearer xY12_-AbCdEfGhIjKlMnOpQrStUv")
    assert "Bearer [redacted:bearer]" in out


def test_scrub_recurses_into_dicts_and_lists() -> None:
    payload = {
        "kw": [
            "sk-1234567890abcdefghij",
            {"nested": "ghp_abcdef1234567890abcdef"},
        ],
    }
    out = scrub(payload)
    assert "[redacted:openai_api_key]" in out["kw"][0]
    assert "[redacted:github_pat]" in out["kw"][1]["nested"]


def test_non_strings_pass_through() -> None:
    assert scrub({"n": 42, "b": True, "x": None}) == {"n": 42, "b": True, "x": None}


def test_unmatched_strings_are_unchanged() -> None:
    payload = "hello world"
    assert scrub(payload) == payload
