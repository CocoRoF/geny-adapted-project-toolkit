"""GAPT-side fallback pricing for `token.tracked` events that arrive
with `cost_usd == 0`.

Why this exists — Phase I.3:

The upstream `geny_executor.stages.s07_token.pricing` lookup uses
*canonical* model ids as dict keys (`claude-sonnet-4-6`, `gpt-4o`, …).
But bundled GAPT manifests configure the api stage with short aliases
(`sonnet`, `haiku`, `opus`) because that's what the Claude Code CLI
accepts. The exact + prefix match both miss → `cost_usd` is reported
as `0.0` even when input/output tokens are real.

We don't want to fork the upstream pricing table — that drifts the
moment Anthropic adjusts prices. So GAPT keeps an alias-only layer
on top of the upstream's `ALL_PRICING`: aliases resolve to a canonical
id; canonical lookups delegate straight to upstream.

This is invoked from `session_registry._update_accumulator` *only* as
a fallback (the payload's own `cost_usd` is trusted when non-zero).
Upstream remains the source of truth for the actual pricing numbers.
"""

from __future__ import annotations

from typing import Final

import structlog


logger = structlog.get_logger(__name__)


# Short → canonical alias map. GAPT-owned, kept in sync with the
# current default models the CLI / SDK adapters resolve to. When the
# user picks a manifest with model="sonnet", the executor stores
# state.model="sonnet" verbatim; we map back to the canonical id so
# the upstream pricing dict matches.
#
# Update policy: when a new bundled manifest variant lands with a
# new short name, add the alias here. Don't add full canonical ids
# (the upstream dict already has them).
_MODEL_ALIASES: Final[dict[str, str]] = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "opus": "claude-opus-4-6",
    # Anthropic's `claude-3-5-sonnet-latest`-style "moving" aliases —
    # the SDK resolves these server-side, but the token stage sees
    # the alias string. Point them at the most recent stable for now.
    "sonnet-latest": "claude-sonnet-4-6",
    "haiku-latest": "claude-haiku-4-5-20251001",
    "opus-latest": "claude-opus-4-6",
}


def resolve_canonical_model(model: str | None) -> str | None:
    """Map a manifest model string (alias or canonical) to the key
    upstream's `ALL_PRICING` uses. Returns None when the string is
    empty / None — the caller then skips the fallback.

    Unknown strings are returned as-is so a canonical id like
    `gpt-4o` (already in upstream's table) goes through untouched.
    """
    if not model:
        return None
    stripped = model.strip()
    if not stripped:
        return None
    return _MODEL_ALIASES.get(stripped, stripped)


def lookup_price(model: str | None) -> dict[str, float] | None:
    """Return `{input, output, cache_write?, cache_read?}` per-million-
    token prices for `model`, or None when no entry exists.

    Delegates to `geny_executor.stages.s07_token.pricing.ALL_PRICING`
    so the actual numbers stay upstream-managed. Local import keeps
    this module cheap when the agent layer is loaded.
    """
    canonical = resolve_canonical_model(model)
    if canonical is None:
        return None
    # Upstream re-export hides the multi-provider unified dict — pull
    # directly from the artifact module. We import lazily so the agent
    # layer doesn't pay for it on every cold start.
    from geny_executor.stages.s07_token.artifact.default.pricing import (  # noqa: PLC0415
        ALL_PRICING,
    )

    prices = ALL_PRICING.get(canonical)
    if prices is None:
        # Prefix-match the same way upstream does — a manifest pointing
        # at `claude-sonnet-4-6-2026XXXX` should still hit the family
        # row. We only run this if the canonical id had no exact match,
        # so cost of the scan stays bounded.
        for key, value in ALL_PRICING.items():
            family_prefix = key.rsplit("-", 1)[0]
            if family_prefix and canonical.startswith(family_prefix):
                return value
        return None
    return prices


def compute_cost_usd(
    *,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_write: int = 0,
    cache_read: int = 0,
) -> float:
    """Compute the dollar cost for one token-tracked snapshot.

    Returns `0.0` when no pricing entry exists for `model` — caller
    decides whether to log that gap (typically WARN once per session
    so a misconfigured manifest is noisy enough to notice).

    Math mirrors upstream's `AnthropicPricingCalculator.calculate`:
    regular input excludes cache reads; cache_write/read use the
    model's own rate when present, otherwise the usual 1.25× / 0.1×
    of the input rate.
    """
    prices = lookup_price(model)
    if prices is None:
        return 0.0
    input_rate = prices["input"]
    output_rate = prices["output"]
    cache_write_rate = prices.get("cache_write", input_rate * 1.25)
    cache_read_rate = prices.get("cache_read", input_rate * 0.1)

    regular_input = max(0, input_tokens - cache_read)
    cost = (regular_input / 1_000_000) * input_rate
    cost += (output_tokens / 1_000_000) * output_rate
    cost += (cache_write / 1_000_000) * cache_write_rate
    cost += (cache_read / 1_000_000) * cache_read_rate
    return cost
