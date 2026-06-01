"""Phase L.4 — `ManifestOverrides.thinking_*` lands in the manifest's
top-level `model` dict + the api stage config so `ModelConfig`
sees it at run time."""

from __future__ import annotations

from gapt_server.agent.environment_service import (
    ManifestOverrides,
    apply_overrides,
)


def _bare_manifest() -> dict:
    """Minimal manifest dict — `apply_overrides` only touches the
    fields it patches, so the rest can be empty."""
    return {
        "model": {},
        "stages": [
            {"name": "input", "config": {}},
            {"name": "api", "config": {"provider": "claude_code_cli"}},
        ],
    }


def test_thinking_budget_writes_both_top_level_and_api_stage() -> None:
    """ModelConfig reads top-level; legacy stages read the api stage
    config dict. We mirror to both, same convention as `model` /
    `max_tokens` in Phase G.4."""
    manifest = _bare_manifest()
    overrides = ManifestOverrides(thinking_budget_tokens=4096)
    patched, applied = apply_overrides(manifest, overrides)

    assert patched["model"]["thinking_budget_tokens"] == 4096
    # budget > 0 implicitly turns thinking on so the operator doesn't
    # need to flip two switches at once.
    assert patched["model"]["thinking_enabled"] is True
    api_stage = next(s for s in patched["stages"] if s["name"] == "api")
    assert api_stage["config"]["thinking_budget_tokens"] == 4096
    assert api_stage["config"]["thinking_enabled"] is True
    assert applied["thinking_budget_tokens"] == 4096
    assert applied["thinking_enabled"] is True


def test_explicit_thinking_disabled_overrides_implicit_enable() -> None:
    """Operator passes budget > 0 *but* sets enabled=False explicitly —
    the explicit disable wins. (Useful for staging a budget without
    actually paying for it.)"""
    manifest = _bare_manifest()
    overrides = ManifestOverrides(
        thinking_enabled=False, thinking_budget_tokens=4096
    )
    patched, applied = apply_overrides(manifest, overrides)

    assert patched["model"]["thinking_enabled"] is False
    assert patched["model"]["thinking_budget_tokens"] == 4096
    assert applied["thinking_enabled"] is False


def test_no_thinking_overrides_leaves_manifest_untouched() -> None:
    """If neither field is set, `apply_overrides` must NOT add the
    keys to the manifest — the manifest's bundled defaults win."""
    manifest = _bare_manifest()
    patched, applied = apply_overrides(manifest, ManifestOverrides())
    assert "thinking_enabled" not in patched["model"]
    assert "thinking_budget_tokens" not in patched["model"]
    assert "thinking_enabled" not in applied
    assert "thinking_budget_tokens" not in applied


def test_thinking_overrides_have_any_returns_true() -> None:
    """`has_any` controls whether the env service bothers patching at
    all — must include the L.4 fields so a thinking-only override
    actually fires."""
    assert ManifestOverrides(thinking_budget_tokens=1024).has_any() is True
    assert ManifestOverrides(thinking_enabled=True).has_any() is True
    assert ManifestOverrides().has_any() is False
