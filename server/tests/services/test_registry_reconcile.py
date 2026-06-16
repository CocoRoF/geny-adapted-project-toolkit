"""Integration tests for the port-reconcile path inside
``ServiceRegistry.start`` using a fake in-container sandbox.

Drives the real registry logic (no docker) to prove that a start whose
intended port is already held inside the container reaps the holder
before spawning — the EADDRINUSE-on-restart fix — and that the three
policies behave as specified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gapt_server.domains.services import ServicePortConflict, ServiceRegistry

if TYPE_CHECKING:
    from pathlib import Path


def _proc_tcp(port: int, *, held: bool) -> bytes:
    """A minimal `/proc/net/tcp; echo ---; /proc/net/tcp6` blob. When
    ``held`` there is one LISTEN (state 0A) row on ``port`` (127.0.0.1),
    otherwise just the headers."""
    header = (
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
        "retrnsmt   uid  timeout inode\n"
    )
    tcp4 = header
    if held:
        tcp4 += (
            f"   0: 0100007F:{port:04X} 00000000:0000 0A "
            "00000000:00000000 00:00000000 00000000  1000        0 12345 1\n"
        )
    return (tcp4 + "---\n" + header).encode()


class _FakeSandbox:
    """Records exec scripts and answers the three call shapes the
    reconcile issues: the /proc listener probe, the free-port script
    (which flips the port to free), and everything else (marker kills,
    pgrep liveness) as a clean rc=0."""

    def __init__(self, *, port: int, held: bool) -> None:
        self.port = port
        self.held = held
        self.scripts: list[str] = []
        self.spawned: list[dict[str, object]] = []

    async def exec(self, argv, *, timeout_s: float = 60.0, **_):  # type: ignore[no-untyped-def]
        script = argv[2] if len(argv) >= 3 else ""
        self.scripts.append(script)
        if "/proc/net/tcp" in script and "echo ---" in script:
            return (0, _proc_tcp(self.port, held=self.held), b"")
        if "inodes=$(awk" in script:
            # The free-port script ran — the holder is now gone.
            self.held = False
            return (0, b"", b"")
        return (0, b"", b"")

    async def spawn_background(self, *, cmd, log_path_inside, env=None, marker=None):  # type: ignore[no-untyped-def]
        self.spawned.append({"cmd": cmd, "marker": marker})
        return -1


class _FakeManager:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self._sandbox = sandbox

    def get(self, workspace_id: str, worktree_path: str) -> _FakeSandbox:
        return self._sandbox


@pytest.mark.asyncio
async def test_start_frees_held_port_then_spawns(tmp_path: Path) -> None:
    sandbox = _FakeSandbox(port=3000, held=True)
    reg = ServiceRegistry(port_conflict_policy="free")
    reg._sandbox_manager = _FakeManager(sandbox)  # type: ignore[assignment]
    try:
        svc = await reg.start(
            workspace_id="ws1",
            label="dev",
            cmd="next dev --turbo -p 3000",
            worktree_path=str(tmp_path),
        )
        assert svc.state.value == "running"
        # The previous-instance markers were reaped (bracketed,
        # non-self-matching form).
        joined = "\n".join(sandbox.scripts)
        assert "[G]APT_SVC=ws1:dev" in joined
        assert "[G]APT_FWD=ws1:dev" in joined
        # The free-port script ran for :3000 and the spawn happened
        # only after the port was freed.
        free_idx = next(i for i, s in enumerate(sandbox.scripts) if "inodes=$(awk" in s)
        assert sandbox.held is False
        assert sandbox.spawned and sandbox.spawned[0]["cmd"] == "next dev --turbo -p 3000"
        # Marker reap precedes the free-port step.
        reap_idx = next(i for i, s in enumerate(sandbox.scripts) if "[G]APT_SVC=ws1:dev" in s)
        assert reap_idx < free_idx
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_start_clear_port_skips_free_script(tmp_path: Path) -> None:
    """When the port is free, only the marker reap runs — no costly
    /proc scan-and-kill."""
    sandbox = _FakeSandbox(port=3000, held=False)
    reg = ServiceRegistry(port_conflict_policy="free")
    reg._sandbox_manager = _FakeManager(sandbox)  # type: ignore[assignment]
    try:
        await reg.start(
            workspace_id="ws1",
            label="dev",
            cmd="next dev -p 3000",
            worktree_path=str(tmp_path),
        )
        assert not any("inodes=$(awk" in s for s in sandbox.scripts)
        assert sandbox.spawned
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_strict_policy_raises_and_releases_slot(tmp_path: Path) -> None:
    """Under strict policy a held port aborts the start with
    ServicePortConflict and leaves no STARTING placeholder behind, so a
    retry isn't wedged."""
    sandbox = _FakeSandbox(port=3000, held=True)
    reg = ServiceRegistry(port_conflict_policy="strict")
    reg._sandbox_manager = _FakeManager(sandbox)  # type: ignore[assignment]
    try:
        with pytest.raises(ServicePortConflict) as ei:
            await reg.start(
                workspace_id="ws1",
                label="dev",
                cmd="next dev -p 3000",
                worktree_path=str(tmp_path),
            )
        assert ei.value.port == 3000
        # Strict never tries to free the port.
        assert not any("inodes=$(awk" in s for s in sandbox.scripts)
        assert not sandbox.spawned
        # Slot released — the label is retryable, not stuck STARTING.
        assert await reg.list("ws1") == []
    finally:
        await reg.aclose()


@pytest.mark.asyncio
async def test_log_hint_drives_reconcile_when_cmd_has_no_port(tmp_path: Path) -> None:
    """`npm run dev` carries no port, but the previous run's log does.
    The reconcile must recover :3000 from that log and free it."""
    log_dir = tmp_path / ".gapt" / "services"
    log_dir.mkdir(parents=True)
    (log_dir / "dev.log").write_text(
        "> next dev --turbo -p 3000\nError: listen EADDRINUSE: address already in use :::3000\n",
        encoding="utf-8",
    )
    sandbox = _FakeSandbox(port=3000, held=True)
    reg = ServiceRegistry(port_conflict_policy="free")
    reg._sandbox_manager = _FakeManager(sandbox)  # type: ignore[assignment]
    try:
        await reg.start(
            workspace_id="ws1",
            label="dev",
            cmd="npm run dev",
            worktree_path=str(tmp_path),
        )
        # Even though the cmd had no port, the log hint (:3000) drove
        # the free-port script.
        assert any("inodes=$(awk" in s for s in sandbox.scripts)
        assert sandbox.held is False
        assert sandbox.spawned
    finally:
        await reg.aclose()
