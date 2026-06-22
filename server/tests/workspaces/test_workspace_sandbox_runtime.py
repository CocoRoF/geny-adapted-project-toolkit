"""Workspace container OCI runtime (`docker run --runtime <x>`).

`GAPT_WORKSPACE_RUNTIME` lets an operator put workspace containers on a
non-default runtime (e.g. `sysbox-runc`). Verify the argv carries `--runtime`
only when set, and that the manager stamps the module-level setting onto each
new handle.
"""

from __future__ import annotations

import pytest

from gapt_server.domains.workspace_sandbox import manager as ws_mod
from gapt_server.domains.workspace_sandbox.manager import (
    WorkspaceSandbox,
    WorkspaceSandboxManager,
)


async def _capture_run_argv(
    monkeypatch: pytest.MonkeyPatch, sandbox: WorkspaceSandbox
) -> list[str]:
    calls: list[list[str]] = []

    async def fake_run_docker(*args: str, timeout_s: float = 30.0):
        calls.append(list(args))
        if args[:2] == ("inspect", "-f"):
            return (1, "", "No such object")
        if args[:2] == ("network", "inspect"):
            return (0, "", "")
        if args[:1] == ("run",):
            return (0, "container-id\n", "")
        return (0, "", "")

    monkeypatch.setattr(ws_mod, "_run_docker", fake_run_docker)
    await sandbox.ensure()
    run_calls = [c for c in calls if c and c[0] == "run"]
    assert len(run_calls) == 1, calls
    return run_calls[0]


@pytest.mark.asyncio
async def test_argv_omits_runtime_when_unset(tmp_path, monkeypatch) -> None:
    sandbox = WorkspaceSandbox(
        workspace_id="01KSWSRT000000000000000000",
        worktree_path=str(tmp_path),
        image="gapt-workspace:latest",
        container_name="gapt-ws-rt0",
        runtime=None,
    )
    argv = await _capture_run_argv(monkeypatch, sandbox)
    assert "--runtime" not in argv


@pytest.mark.asyncio
async def test_argv_includes_runtime_when_set(tmp_path, monkeypatch) -> None:
    sandbox = WorkspaceSandbox(
        workspace_id="01KSWSRT000000000000000001",
        worktree_path=str(tmp_path),
        image="gapt-workspace:latest",
        container_name="gapt-ws-rt1",
        runtime="sysbox-runc",
    )
    argv = await _capture_run_argv(monkeypatch, sandbox)
    idx = argv.index("--runtime")
    assert argv[idx + 1] == "sysbox-runc"
    # --runtime must precede the image (positional) in the argv.
    assert idx < argv.index("gapt-workspace:latest")


def test_manager_stamps_runtime_from_module_setting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ws_mod, "GAPT_WORKSPACE_RUNTIME", "sysbox-runc")
    mgr = WorkspaceSandboxManager()
    sandbox = mgr.get("01KSWSRT000000000000000010", str(tmp_path))
    assert sandbox.runtime == "sysbox-runc"


def test_manager_no_runtime_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ws_mod, "GAPT_WORKSPACE_RUNTIME", "")
    mgr = WorkspaceSandboxManager()
    sandbox = mgr.get("01KSWSRT000000000000000011", str(tmp_path))
    assert sandbox.runtime is None
