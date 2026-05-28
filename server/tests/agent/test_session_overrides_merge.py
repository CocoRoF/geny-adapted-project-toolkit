"""Phase G.4 — `_merge_overrides()` correctness.

Per-session overrides win over global admin prefs; missing fields
fall through. Tests target the pure helper since the rest of
session_manager already has integration coverage."""

from __future__ import annotations

from gapt_server.agent.environment_service import ManifestOverrides
from gapt_server.agent.session_manager import _merge_overrides


def test_both_none_returns_none() -> None:
    """Empty base + empty patch → None so `instantiate_pipeline`
    sees the same shape as when nothing is configured."""
    assert _merge_overrides(None, None) is None


def test_only_base_returns_base() -> None:
    base = ManifestOverrides(model="opus", max_tokens=4096)
    assert _merge_overrides(base, None) is base


def test_only_patch_returns_patch() -> None:
    patch = ManifestOverrides(model="sonnet")
    assert _merge_overrides(None, patch) is patch


def test_patch_field_overrides_base() -> None:
    """Per-session `model` wins over the global pref. Other fields
    inherit from base."""
    base = ManifestOverrides(model="opus", max_tokens=4096, timeout_s=180)
    patch = ManifestOverrides(model="sonnet")
    merged = _merge_overrides(base, patch)
    assert merged is not None
    assert merged.model == "sonnet"
    assert merged.max_tokens == 4096
    assert merged.timeout_s == 180


def test_patch_missing_field_falls_through() -> None:
    """`None` in patch means "don't touch this field" — base wins."""
    base = ManifestOverrides(model="opus", max_tokens=4096)
    patch = ManifestOverrides(max_tokens=8192)
    merged = _merge_overrides(base, patch)
    assert merged is not None
    assert merged.model == "opus"
    assert merged.max_tokens == 8192


def test_patch_with_zero_cost_budget_overrides() -> None:
    """`0.0` is a legitimate value (operator wants to disable spend),
    not "missing". Must override base."""
    base = ManifestOverrides(cost_budget_usd=5.0)
    patch = ManifestOverrides(cost_budget_usd=0.0)
    merged = _merge_overrides(base, patch)
    assert merged is not None
    assert merged.cost_budget_usd == 0.0


def test_all_fields_can_be_overridden() -> None:
    base = ManifestOverrides(
        model="opus",
        max_tokens=4096,
        max_iterations=10,
        cost_budget_usd=1.0,
        timeout_s=120,
    )
    patch = ManifestOverrides(
        model="haiku",
        max_tokens=2048,
        max_iterations=5,
        cost_budget_usd=0.5,
        timeout_s=60,
    )
    merged = _merge_overrides(base, patch)
    assert merged is not None
    assert merged.model == "haiku"
    assert merged.max_tokens == 2048
    assert merged.max_iterations == 5
    assert merged.cost_budget_usd == 0.5
    assert merged.timeout_s == 60
