"""Payload-mask helper.

Before an audit event hits Postgres we run its JSON payload through
this scrub pass. The goal is to keep obvious plaintext secrets from
ever landing in a queryable audit row — the secret vault is the only
place secrets should be readable in plaintext.

Heuristics are intentionally narrow: an over-eager scrub would hide
useful debug info. Add patterns only when we have a real format to
match (Anthropic / OpenAI / GitHub / Slack / generic Bearer tokens).
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Anthropic API keys
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{30,}"), "[redacted:anthropic_api_key]"),
    # OpenAI API keys (both old `sk-` and newer `sk-proj-` forms)
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"), "[redacted:openai_api_key]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[redacted:openai_api_key]"),
    # GitHub personal / fine-grained tokens
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "[redacted:github_pat]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[redacted:github_fine_grained_pat]"),
    # Slack bot tokens
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[redacted:slack_token]"),
    # Generic Bearer
    (re.compile(r"(?i)bearer\s+([A-Za-z0-9._-]{20,})"), "Bearer [redacted:bearer]"),
)


def scrub(value: Any) -> Any:
    """Recursively redact known secret patterns inside any JSON-like
    structure.

    Strings are scrubbed in place; dicts and lists are walked. Other
    scalar types are returned unchanged.
    """
    if isinstance(value, str):
        out = value
        for pattern, replacement in _PATTERNS:
            out = pattern.sub(replacement, out)
        return out
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value
