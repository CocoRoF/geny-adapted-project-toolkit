"""Unit tests for the workspace-sandbox capability probe."""

from __future__ import annotations

import shutil

import pytest

from gapt_server.domains.sandbox import capabilities
from gapt_server.domains.sandbox.capabilities import probe_capabilities


class _FakeImages:
    def __init__(self, present: bool) -> None:
        self._present = present

    def get(self, tag: str):
        if not self._present:
            raise RuntimeError(f"image {tag} not found")
        return object()


class _FakeClient:
    def __init__(self, *, runtimes: dict, image_present: bool) -> None:
        self._runtimes = runtimes
        self.images = _FakeImages(image_present)
        self.closed = False

    def info(self) -> dict:
        return {"ServerVersion": "29.0", "Runtimes": self._runtimes}

    def close(self) -> None:
        self.closed = True


def _patch(monkeypatch, *, docker_on_path=True, client=None, raises=False) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if docker_on_path else None)

    def _make():
        if raises:
            raise RuntimeError("Cannot connect to the Docker daemon socket")
        return client

    monkeypatch.setattr("gapt_server.domains.sandbox.sysbox_backend.make_default_client", _make)


def _by_key(report) -> dict:
    return {c.key: c for c in report.capabilities}


@pytest.mark.asyncio
async def test_all_present_is_ready(monkeypatch) -> None:
    _patch(
        monkeypatch,
        client=_FakeClient(runtimes={"runc": {}, "sysbox-runc": {}}, image_present=True),
    )
    report = await probe_capabilities(runtime="sysbox-runc", image="gapt-workspace:latest")
    assert report.workspaces_ready is True
    assert all(c.state == "ok" for c in report.capabilities)
    assert report.missing == []


@pytest.mark.asyncio
async def test_sysbox_missing(monkeypatch) -> None:
    _patch(monkeypatch, client=_FakeClient(runtimes={"runc": {}}, image_present=True))
    report = await probe_capabilities(runtime="sysbox-runc", image="gapt-workspace:latest")
    assert report.workspaces_ready is False
    rt = _by_key(report)["sandbox_runtime"]
    assert rt.state == "missing"
    assert "sysbox" in (rt.remedy or "").lower()


@pytest.mark.asyncio
async def test_image_missing(monkeypatch) -> None:
    _patch(
        monkeypatch,
        client=_FakeClient(runtimes={"sysbox-runc": {}}, image_present=False),
    )
    report = await probe_capabilities(runtime="sysbox-runc", image="gapt-workspace:latest")
    assert report.workspaces_ready is False
    img = _by_key(report)["workspace_image"]
    assert img.state == "missing"
    assert "build.sh" in (img.remedy or "")


@pytest.mark.asyncio
async def test_docker_cli_missing(monkeypatch) -> None:
    _patch(
        monkeypatch,
        docker_on_path=False,
        client=_FakeClient(runtimes={"sysbox-runc": {}}, image_present=True),
    )
    report = await probe_capabilities(runtime="sysbox-runc", image="gapt-workspace:latest")
    assert report.workspaces_ready is False
    assert _by_key(report)["docker_cli"].state == "missing"


@pytest.mark.asyncio
async def test_daemon_unreachable_degrades_downstream(monkeypatch) -> None:
    _patch(monkeypatch, raises=True)
    report = await probe_capabilities(runtime="sysbox-runc", image="gapt-workspace:latest")
    assert report.workspaces_ready is False
    by = _by_key(report)
    assert by["docker_daemon"].state == "missing"
    # Runtime + image can't be checked once the daemon is down.
    assert by["sandbox_runtime"].state == "degraded"
    assert by["workspace_image"].state == "degraded"


@pytest.mark.asyncio
async def test_report_json_shape(monkeypatch) -> None:
    _patch(
        monkeypatch,
        client=_FakeClient(runtimes={"sysbox-runc": {}}, image_present=True),
    )
    report = await probe_capabilities(runtime="sysbox-runc", image="gapt-workspace:latest")
    data = report.to_json()
    assert set(data) == {"workspaces_ready", "capabilities"}
    assert {c["key"] for c in data["capabilities"]} == set(capabilities.REQUIRED_KEYS)
    for c in data["capabilities"]:
        assert set(c) == {"key", "label", "state", "detail", "remedy"}
