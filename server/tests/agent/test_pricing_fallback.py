"""Phase I.3 unit — model alias → canonical id → price lookup.

These tests are pure (no DB / no executor) so the alias table can be
exercised without spinning up a pipeline."""

from __future__ import annotations

import pytest

from gapt_server.agent.pricing import (
    compute_cost_usd,
    lookup_price,
    resolve_canonical_model,
)


# ───────────────────────────────────────── alias map ──


def test_anthropic_aliases_resolve_to_canonical() -> None:
    assert resolve_canonical_model("sonnet") == "claude-sonnet-4-6"
    assert resolve_canonical_model("haiku") == "claude-haiku-4-5-20251001"
    assert resolve_canonical_model("opus") == "claude-opus-4-6"


def test_canonical_id_passes_through_unchanged() -> None:
    """A manifest using the full id should hit upstream's table
    directly — the resolver only translates known short aliases."""
    assert resolve_canonical_model("claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert resolve_canonical_model("gpt-4o") == "gpt-4o"


def test_empty_and_none_handled() -> None:
    assert resolve_canonical_model(None) is None
    assert resolve_canonical_model("") is None
    assert resolve_canonical_model("   ") is None


# ─────────────────────────────────────── price lookup ──


def test_anthropic_alias_finds_price() -> None:
    prices = lookup_price("sonnet")
    assert prices is not None
    assert prices["input"] > 0
    assert prices["output"] > prices["input"]  # output always ≥ input


def test_openai_canonical_finds_price() -> None:
    assert lookup_price("gpt-4o") is not None


def test_google_canonical_finds_price() -> None:
    assert lookup_price("gemini-2.5-flash") is not None


def test_unknown_model_returns_none() -> None:
    assert lookup_price("totally-made-up") is None


# ────────────────────────────────────── compute_cost ──


def test_sonnet_cost_matches_per_million() -> None:
    """Sonnet 4.6: $3/M input, $15/M output."""
    cost = compute_cost_usd(
        model="sonnet", input_tokens=1_000_000, output_tokens=1_000_000
    )
    # 3.0 + 15.0 = 18.0
    assert cost == pytest.approx(18.0, rel=1e-3)


def test_unknown_model_falls_back_to_zero() -> None:
    """Pricing miss must NOT raise — caller treats 0.0 as "log + move
    on" so a misconfigured manifest doesn't crash the live chat."""
    assert (
        compute_cost_usd(
            model="not-a-model", input_tokens=100, output_tokens=100
        )
        == 0.0
    )


def test_cache_read_excluded_from_regular_input() -> None:
    """Cache reads are billed at ~10% of input — the regular portion
    of `input_tokens` must subtract them so we don't double-charge."""
    no_cache = compute_cost_usd(model="sonnet", input_tokens=1000, output_tokens=0)
    with_cache = compute_cost_usd(
        model="sonnet", input_tokens=1000, output_tokens=0, cache_read=1000
    )
    # All input was cache-read → cost should be ~10% of the no-cache run.
    assert with_cache < no_cache
    assert with_cache == pytest.approx(no_cache * 0.1, rel=1e-3)
