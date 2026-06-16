"""Boot-reconcile recovery tests: `ServiceRegistry.reconcile_workspaces`
converging a workspace container to its durable manifests.

Proves the three recovery actions the user signed off on:
  - alive  → adopt with FULL metadata (cmd/port/env/expose restored)
  - dead + desired running → restart (start() reconciles the port)
  - dead + desired stopped → surface as EXITED, never auto-start
plus the legacy adopt of a marker-alive service that has no manifest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gapt_server.domains.services import ServiceRegistry
from gapt_server.domains.services.manifest import (
    DESIRED_RUNNING,
    DESIRED_STOPPED,
    ServiceManifest,
    read_manifest,
    write_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


def _bracket(marker: str) -> str:
    return f"[{marker[0]}]{marker[1:]}"


def _proc_tcp(port: int, *, held: bool) -> bytes:
    header = "  sl  local_address rem_address st ... inode\n"
    tcp4 = header
    if held:
        tcp4 += f"   0: 0100007F:{port:04X} 00000000:0000 0A 0 0 0 0 0 12345 1\n"
    return (tcp4 + "---\n" + header).encode()


class _FakeSandbox:
    """Answers the call shapes reconcile + start issue. ``alive_markers``
    is the set of plain ``GAPT_SVC=...`` markers whose process is live;
    ``legacy`` is raw `pgrep -af` output for marker-alive services that
    have no manifest."""

    def __init__(
        self,
        *,
        alive_markers: set[str] | None = None,
        legacy: str = "",
        port_held: bool = False,
    ) -> None:
        self.alive = alive_markers or set()
        self.legacy = legacy
        self.held = port_held
        self.scripts: list[str] = []
        self.spawned: list[str] = []

    async def exec(self, argv, *, timeout_s: float = 60.0, **_):  # type: ignore[no-untyped-def]
        script = argv[2] if len(argv) >= 3 else ""
        self.scripts.append(script)
        if "inodes=$(awk" in script:  # free-port script
            self.held = False
            return (0, b"", b"")
        if "/proc/net/tcp" in script and "echo ---" in script:  # listener probe
            return (0, _proc_tcp(3000, held=self.held), b"")
        if "pgrep -af" in script:  # legacy marker scan
            return (0, self.legacy.encode(), b"")
        if "pgrep -f " in script and ">/dev/null" in script and "kill -" not in script:
            # Single-marker liveness check.
            alive = any(_bracket(m) in script for m in self.alive)
            return (0 if alive else 1, b"", b"")
        return (0, b"", b"")  # marker kill scripts, etc.

    async def spawn_background(self, *, cmd, log_path_inside, env=None, marker=None):  # type: ignore[no-untyped-def]
        self.spawned.append(cmd)
        return -1


class _FakeManager:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self._sandbox = sandbox

    def get(self, workspace_id: str, worktree_path: str) -> _FakeSandbox:
        return self._sandbox


def _reg(sandbox: _FakeSandbox) -> ServiceRegistry:
    reg = ServiceRegistry(port_conflict_policy="free")
    reg._sandbox_manager = _FakeManager(sandbox)  # type: ignore[assignment]
    return reg


@pytest.mark.asyncio
async def test_alive_service_adopted_with_full_metadata(tmp_path: Path) -> None:
    write_manifest(
        str(tmp_path),
        ServiceManifest(
            label="dev",
            cmd="npm run dev",
            user_port=3000,
            env={"DATABASE_URL": "x"},
            expose={"host": "h", "url": "https://h"},
        ),
    )
    sandbox = _FakeSandbox(alive_markers={"GAPT_SVC=ws1:dev"})
    reg = _reg(sandbox)
    try:
        report = await reg.reconcile_workspaces([("ws1", str(tmp_path))])
        assert [(r.label, r.action) for r in report] == [("dev", "adopted")]
        svc = await reg.get("ws1", "dev")
        assert svc.state.value == "running"
        # Full metadata restored — not the lossy marker-scan version.
        assert svc.cmd == "npm run dev"
        assert svc.user_port == 3000
        assert svc.env == {"DATABASE_URL": "x"}
        assert svc.bound_host == "h"
        assert svc.bound_url == "https://h"
        # Adopt must NOT spawn a new process.
        assert sandbox.spawned == []
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_dead_desired_running_is_restarted(tmp_path: Path) -> None:
    write_manifest(
        str(tmp_path),
        ServiceManifest(label="dev", cmd="next dev -p 3000", desired_state=DESIRED_RUNNING),
    )
    # Marker not alive + a zombie still holds :3000 → restart must
    # reconcile the port then spawn.
    sandbox = _FakeSandbox(alive_markers=set(), port_held=True)
    reg = _reg(sandbox)
    try:
        report = await reg.reconcile_workspaces([("ws1", str(tmp_path))])
        assert [(r.label, r.action) for r in report] == [("dev", "restarted")]
        svc = await reg.get("ws1", "dev")
        assert svc.state.value == "running"
        assert sandbox.spawned == ["next dev -p 3000"]
        # The held port was freed during the restart.
        assert any("inodes=$(awk" in s for s in sandbox.scripts)
        # Manifest re-asserted as running.
        assert read_manifest(str(tmp_path), "dev").desired_state == DESIRED_RUNNING
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_dead_desired_stopped_is_kept_not_restarted(tmp_path: Path) -> None:
    write_manifest(
        str(tmp_path),
        ServiceManifest(label="dev", cmd="npm run dev", desired_state=DESIRED_STOPPED),
    )
    sandbox = _FakeSandbox(alive_markers=set())
    reg = _reg(sandbox)
    try:
        report = await reg.reconcile_workspaces([("ws1", str(tmp_path))])
        assert [(r.label, r.action) for r in report] == [("dev", "kept_stopped")]
        svc = await reg.get("ws1", "dev")
        assert svc.state.value == "exited"
        # Never spawned.
        assert sandbox.spawned == []
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_legacy_marker_without_manifest_is_adopted(tmp_path: Path) -> None:
    """A service with a live marker but no manifest (started before this
    feature) is adopted and back-filled with a manifest."""
    # `pgrep -af` line: `<pid> <inner-cmd> <marker>`. Only the marker
    # tail (wid:label) is load-bearing for the legacy adopt.
    legacy = "4242 sh -c run-it GAPT_SVC=ws1:old"
    sandbox = _FakeSandbox(alive_markers={"GAPT_SVC=ws1:old"}, legacy=legacy)
    reg = _reg(sandbox)
    try:
        report = await reg.reconcile_workspaces([("ws1", str(tmp_path))])
        assert [(r.label, r.action) for r in report] == [("old", "adopted")]
        svc = await reg.get("ws1", "old")
        assert svc.state.value == "running"
        # A manifest was back-filled so the next boot is clean.
        m = read_manifest(str(tmp_path), "old")
        assert m is not None and m.desired_state == DESIRED_RUNNING
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_start_writes_manifest(tmp_path: Path) -> None:
    sandbox = _FakeSandbox()
    reg = _reg(sandbox)
    try:
        await reg.start(
            workspace_id="ws1",
            label="dev",
            cmd="next dev -p 3000",
            worktree_path=str(tmp_path),
            port=3000,
            env={"FOO": "bar"},
        )
        m = read_manifest(str(tmp_path), "dev")
        assert m is not None
        assert m.cmd == "next dev -p 3000"
        assert m.user_port == 3000
        assert m.env == {"FOO": "bar"}
        assert m.desired_state == DESIRED_RUNNING
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_user_stop_marks_desired_stopped(tmp_path: Path) -> None:
    sandbox = _FakeSandbox()
    reg = _reg(sandbox)
    try:
        await reg.start(
            workspace_id="ws1", label="dev", cmd="npm run dev", worktree_path=str(tmp_path)
        )
        await reg.stop("ws1", "dev")
        assert read_manifest(str(tmp_path), "dev").desired_state == DESIRED_STOPPED
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_kill_all_keeps_desired_running(tmp_path: Path) -> None:
    """GAPT shutdown must NOT mark services stopped — they're still
    desired running and the next boot's reconcile brings them back."""
    sandbox = _FakeSandbox()
    reg = _reg(sandbox)
    try:
        await reg.start(
            workspace_id="ws1", label="dev", cmd="npm run dev", worktree_path=str(tmp_path)
        )
        await reg.kill_all()
        assert read_manifest(str(tmp_path), "dev").desired_state == DESIRED_RUNNING
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_remove_deletes_manifest(tmp_path: Path) -> None:
    sandbox = _FakeSandbox()
    reg = _reg(sandbox)
    try:
        await reg.start(
            workspace_id="ws1", label="dev", cmd="npm run dev", worktree_path=str(tmp_path)
        )
        await reg.stop("ws1", "dev")
        await reg.remove("ws1", "dev")
        assert read_manifest(str(tmp_path), "dev") is None
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_reconcile_is_idempotent(tmp_path: Path) -> None:
    write_manifest(str(tmp_path), ServiceManifest(label="dev", cmd="npm run dev"))
    sandbox = _FakeSandbox(alive_markers={"GAPT_SVC=ws1:dev"})
    reg = _reg(sandbox)
    try:
        first = await reg.reconcile_workspaces([("ws1", str(tmp_path))])
        assert len(first) == 1
        # Second pass: already in the registry → nothing re-adopted.
        second = await reg.reconcile_workspaces([("ws1", str(tmp_path))])
        assert second == []
    finally:
        await reg.aclose()
