"""geny-executor 2.2.0 `validate_manifest` over GAPT's manifests.

Two guarantees pinned here:

1. Every bundled manifest still loads + validates with zero *errors*
   under 2.2.0's stricter write-time validation (the release notes
   verified 0-error; this keeps it true as GAPT edits them).
2. The override-patch path (`apply_overrides`) introduces no NEW
   findings relative to the unpatched manifest — in particular no
   `model.dual_home` warning, which the pre-2.2 dual-write produced.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from geny_executor import EnvironmentManifest, validate_manifest

from gapt_server.agent.environment_service import (
    SERVER_MANIFESTS_DIR,
    ManifestOverrides,
    apply_overrides,
)

if TYPE_CHECKING:
    from pathlib import Path

BUNDLED = sorted(SERVER_MANIFESTS_DIR.glob("*.json"))


def _load(path: Path) -> EnvironmentManifest:
    return EnvironmentManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _codes(manifest: EnvironmentManifest) -> list[str]:
    return [issue.code for issue in validate_manifest(manifest)]


@pytest.mark.parametrize("path", BUNDLED, ids=[p.stem for p in BUNDLED])
def test_bundled_manifest_has_no_validation_errors(path: Path) -> None:
    issues = validate_manifest(_load(path))
    errors = [i for i in issues if i.severity == "error"]
    assert not errors, [i.to_dict() for i in errors]


@pytest.mark.parametrize("path", BUNDLED, ids=[p.stem for p in BUNDLED])
def test_override_patched_manifest_adds_no_new_findings(path: Path) -> None:
    """Patch each bundled manifest with a representative override set
    (model + max_tokens + thinking — the fields the dual-write used to
    mirror into the api stage config) and assert the patched output's
    finding-code multiset is a subset of the unpatched manifest's.
    The hoist in `apply_overrides` may *remove* findings (stage-config
    model keys were schema-unknown); it must never add one."""
    baseline_codes = _codes(_load(path))

    raw = json.loads(path.read_text(encoding="utf-8"))
    patched_dict, applied = apply_overrides(
        raw,
        ManifestOverrides(
            model="claude-opus-4-7",
            max_tokens=30_000,
            thinking_enabled=True,
            thinking_budget_tokens=8_192,
            max_iterations=25,
        ),
    )
    assert applied["model"] == "claude-opus-4-7"
    patched_codes = _codes(EnvironmentManifest.from_dict(patched_dict))

    new = [c for c in patched_codes if patched_codes.count(c) > baseline_codes.count(c)]
    assert not new, f"override patching introduced new findings: {new}"
    # The dual-home warning specifically must never come back.
    assert "model.dual_home" not in patched_codes
