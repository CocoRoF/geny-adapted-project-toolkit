"""Phase L.4 (updated for geny-executor 2.2.0) — `ManifestOverrides.
thinking_*` lands in the manifest's *top-level* `model` dict, the
single home `validate_manifest` documents. The api-stage copy is no
longer written (it was always inert and now draws a `model.dual_home`
warning); any pre-existing stage-config copy is hoisted into the
top-level block so the manifest author's intent stays effective."""

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


def test_thinking_budget_writes_top_level_only() -> None:
    """2.2.0 single home: ModelConfig reads the top-level `model`
    block; the api stage config must NOT receive a (dual-home) copy."""
    manifest = _bare_manifest()
    overrides = ManifestOverrides(thinking_budget_tokens=4096)
    patched, applied = apply_overrides(manifest, overrides)

    assert patched["model"]["thinking_budget_tokens"] == 4096
    # budget > 0 implicitly turns thinking on so the operator doesn't
    # need to flip two switches at once.
    assert patched["model"]["thinking_enabled"] is True
    api_stage = next(s for s in patched["stages"] if s["name"] == "api")
    assert "thinking_budget_tokens" not in api_stage["config"]
    assert "thinking_enabled" not in api_stage["config"]
    assert applied["thinking_budget_tokens"] == 4096
    assert applied["thinking_enabled"] is True


def test_stage_config_model_is_hoisted_to_top_level() -> None:
    """A bundled manifest carrying `model` / `max_tokens` in the api
    stage config (the pre-2.2 GAPT shape) must come out single-homed:
    the values move to the top-level block (where the executor reads
    them) unless an override stomps them, and the stage-config copies
    are removed so `validate_manifest` raises no `model.dual_home`."""
    manifest = {
        "model": {},
        "stages": [
            {
                "name": "api",
                "config": {
                    "provider": "claude_code_cli",
                    "model": "sonnet",
                    "max_tokens": 8192,
                },
            },
        ],
    }
    patched, applied = apply_overrides(
        manifest, ManifestOverrides(max_tokens=30_000)
    )

    api_stage = next(s for s in patched["stages"] if s["name"] == "api")
    assert "model" not in api_stage["config"]
    assert "max_tokens" not in api_stage["config"]
    # Non-overridden field keeps the manifest's bundled value …
    assert patched["model"]["model"] == "sonnet"
    # … and the override wins where given.
    assert patched["model"]["max_tokens"] == 30_000
    assert applied == {"max_tokens": 30_000}


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
