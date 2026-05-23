"""GaptEnvironmentService — 3-tier manifest resolution + pipeline boot.

The pipeline boot test asserts only that `from_manifest_async` returns
a usable `Pipeline` object; we don't run a real LLM call here (that's
the M0-P3 PoC's job). Tests are hermetic — no Postgres, no docker.
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003  — pytest fixture annotation

import pytest

from gapt_server.agent.environment_service import (
    SERVER_MANIFESTS_DIR,
    GaptEnvironmentService,
    ManifestNotFoundError,
)


@pytest.fixture
def svc() -> GaptEnvironmentService:
    return GaptEnvironmentService()


def test_three_bundled_manifests_exist(svc: GaptEnvironmentService) -> None:
    bundled = svc.list_bundled()
    assert set(bundled) >= {"gapt_default", "gapt_planning", "gapt_review"}


@pytest.mark.parametrize("env_id", ["gapt_default", "gapt_planning", "gapt_review"])
def test_bundled_manifest_resolves(env_id: str, svc: GaptEnvironmentService) -> None:
    resolution = svc.resolve(env_id)
    assert resolution.source == "server_bundled"
    assert resolution.path == SERVER_MANIFESTS_DIR / f"{env_id}.json"
    # The manifest itself is loaded — the executor's strict load runs
    # only when from_manifest_async is invoked.
    assert resolution.manifest is not None


def test_unknown_manifest_raises_with_attempted_paths(svc: GaptEnvironmentService) -> None:
    with pytest.raises(ManifestNotFoundError) as exc_info:
        svc.resolve("does_not_exist")
    err = exc_info.value
    assert err.env_id == "does_not_exist"
    # Only the bundled path is attempted when neither workspace nor
    # project override is supplied.
    assert len(err.tried) == 1
    assert err.tried[0].name == "does_not_exist.json"


def test_empty_env_id_raises_with_no_paths(svc: GaptEnvironmentService) -> None:
    with pytest.raises(ManifestNotFoundError) as exc_info:
        svc.resolve("   ")
    assert exc_info.value.tried == []


def test_project_override_path_wins(tmp_path: Path, svc: GaptEnvironmentService) -> None:
    # Build a fake override that has a recognisable metadata tag.
    override_dir = tmp_path / "override"
    override_dir.mkdir()
    override_path = override_dir / "custom.json"
    with (SERVER_MANIFESTS_DIR / "gapt_default.json").open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["metadata"]["tags"] = ["test-override-marker"]
    override_path.write_text(json.dumps(data), encoding="utf-8")

    resolution = svc.resolve("gapt_default", project_override_path=override_path)
    assert resolution.source == "project_override"
    assert resolution.path == override_path


def test_workspace_local_wins_over_bundled(tmp_path: Path, svc: GaptEnvironmentService) -> None:
    workspace = tmp_path / "ws"
    local_dir = workspace / ".gapt" / "manifests"
    local_dir.mkdir(parents=True)
    with (SERVER_MANIFESTS_DIR / "gapt_default.json").open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["metadata"]["tags"] = ["test-workspace-local-marker"]
    (local_dir / "gapt_default.json").write_text(json.dumps(data), encoding="utf-8")

    resolution = svc.resolve("gapt_default", workspace_dir=workspace)
    assert resolution.source == "workspace_local"
    assert resolution.path is not None
    assert resolution.path.parent == local_dir


def test_project_override_takes_priority_over_workspace(
    tmp_path: Path, svc: GaptEnvironmentService
) -> None:
    # Both override + workspace-local exist; override wins.
    workspace = tmp_path / "ws"
    local_dir = workspace / ".gapt" / "manifests"
    local_dir.mkdir(parents=True)
    bundled_text = (SERVER_MANIFESTS_DIR / "gapt_default.json").read_text(encoding="utf-8")
    (local_dir / "gapt_default.json").write_text(bundled_text, encoding="utf-8")

    override_path = tmp_path / "override.json"
    override_path.write_text(bundled_text, encoding="utf-8")

    resolution = svc.resolve(
        "gapt_default",
        workspace_dir=workspace,
        project_override_path=override_path,
    )
    assert resolution.source == "project_override"


@pytest.mark.asyncio
async def test_instantiate_pipeline_boots(svc: GaptEnvironmentService) -> None:
    pipeline = await svc.instantiate_pipeline("gapt_default")
    # 21-stage pipeline per the executor 2.1.0 contract.
    descriptions = pipeline.describe()
    assert len(descriptions) == 21
    stage_names = [s.name for s in descriptions]
    assert "api" in stage_names
    assert "tool" in stage_names
