"""In-process registry of managed background services.

Each `Service` wraps a process running *inside* the workspace's
sandbox container, with stdout/stderr appended to a log file at
`/workspace/.gapt/services/<label>.log` — the bind-mount surfaces
the same file on the host at `<worktree>/.gapt/services/<label>.log`,
so the existing host-side `file-tail` SSE endpoint just works.

The registry is keyed by (workspace_id, label) so two workspaces can
both have a `web` service without collision.

Concurrency model
-----------------
- `start()` is async-safe via the registry's lock.
- The reaper task polls every 500 ms — checks each service's
  alive-state via `docker exec ... pgrep` against the recorded cmd
  pattern, flips state when the in-container process exits.
- `expose()` is a *binding* concern (Caddy admin) and is handled by
  the router on top of this primitive — the registry just records
  the bound URL so the GET response carries it.

Port detection scans the first 16 KB of the log file (host-side) for
common "server started on :PORT" patterns.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import structlog

from gapt_server.domains.workspace_sandbox import (
    WorkspaceSandbox,
    WorkspaceSandboxError,
    WorkspaceSandboxManager,
)

logger = structlog.get_logger(__name__)


# Capture loop's poll cadence — service state transitions surface to
# the UI at the *max* of this and the UI's own poll interval.
_REAP_INTERVAL_S = 0.5

# Auto-port: scan the first 16 KB of log output. Most dev servers
# print their listen URL in the first 1-2 KB so this is generous.
_PORT_SCAN_BYTES = 16 * 1024

# Bog-standard dev-server messages: "ready - started server on
# 0.0.0.0:3000", "Local: http://localhost:5173/", "Listening on
# port 8080", "Uvicorn running on http://127.0.0.1:8000". One regex
# catches them all without being fragile.
_PORT_PATTERN = re.compile(
    r"(?:"
    r"(?:https?://[\w\.-]+:|localhost:|0\.0\.0\.0:|127\.0\.0\.1:|on port |listening on :)"
    r")(\d{2,5})\b",
    re.IGNORECASE,
)


class ServiceState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    STOPPING = "stopping"


class ServiceNotFound(KeyError):
    pass


class ServiceAlreadyExists(RuntimeError):
    pass


@dataclass
class Service:
    """A single managed process inside the workspace sandbox.
    Mutable — the registry owns the only reference, callers get
    snapshots via `Service.snapshot()`."""

    workspace_id: str
    label: str
    cmd: str  # full command line; runs inside `sh -c '...'` in the container
    worktree_path: str  # host-side worktree dir bind-mounted at /workspace
    log_path: str  # host-side log file path (under worktree)
    port: int | None = None
    state: ServiceState = ServiceState.STARTING
    started_at: float = field(default_factory=time.time)
    exited_at: float | None = None
    exit_code: int | None = None
    bound_url: str | None = None  # set when expose() succeeds
    bound_host: str | None = None
    auto_port: int | None = None  # what the scanner found
    # The container-side pgrep pattern we use to alive-check the
    # service. Stored at start-time because `Service.cmd` is the
    # full shell line and pgrep would match too broadly otherwise.
    # We tag every spawned cmd with a unique marker the pattern
    # matches on.
    _pgrep_marker: str = ""
    # Compatibility shim — earlier code accessed `.pid` to display
    # something to the UI. Containers don't expose host-PIDs cleanly
    # so we always emit None and let `state` carry the alive signal.
    pid: int | None = None

    def snapshot(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "label": self.label,
            "cmd": self.cmd,
            "port": self.port,
            "auto_port": self.auto_port,
            "pid": self.pid,
            "state": self.state.value,
            "started_at": self.started_at,
            "exited_at": self.exited_at,
            "exit_code": self.exit_code,
            "bound_url": self.bound_url,
            "bound_host": self.bound_host,
            "log_path": self.log_path,
        }


class ServiceRegistry:
    """One per process. Lock guards the dict; per-service operations
    don't need cross-service synchronisation."""

    def __init__(self, sandbox_manager: WorkspaceSandboxManager | None = None) -> None:
        self._entries: dict[tuple[str, str], Service] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None
        self._closed = False
        # Injected at container build-time so the registry can spawn
        # in-sandbox commands. Tests can omit it (or pass a stub) —
        # `start()` raises a clear error if it's None and a real
        # spawn is attempted.
        self._sandbox_manager = sandbox_manager

    def bind_sandbox_manager(self, manager: WorkspaceSandboxManager) -> None:
        """Late-bind (used when the registry is built before the
        manager, e.g. dataclass-default ordering in `AppContainer`)."""
        self._sandbox_manager = manager

    def _ensure_reaper(self) -> None:
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        loop = asyncio.get_event_loop()
        self._reaper_task = loop.create_task(self._reap_loop(), name="services-reaper")

    async def _reap_loop(self) -> None:
        while not self._closed:
            try:
                async with self._lock:
                    services = list(self._entries.values())
                for svc in services:
                    if svc.state not in {ServiceState.STARTING, ServiceState.RUNNING}:
                        # Already exited / stopped — still try to scan
                        # port from the log in case it landed late.
                        if svc.auto_port is None:
                            self._scan_port(svc)
                        continue
                    if self._sandbox_manager is None:
                        continue
                    # pgrep against the container to see if the
                    # process is still alive. -f matches the full
                    # cmdline so our unique marker hits.
                    sandbox = self._sandbox_manager.get(
                        svc.workspace_id, svc.worktree_path
                    )
                    try:
                        rc, _, _ = await sandbox.exec(
                            ["sh", "-c", f"pgrep -f {shlex.quote(svc._pgrep_marker)} >/dev/null"],
                            timeout_s=3.0,
                        )
                    except WorkspaceSandboxError:
                        # Sandbox went away (container removed) — flip
                        # the service to FAILED so the UI shows
                        # something useful.
                        svc.state = ServiceState.FAILED
                        svc.exited_at = time.time()
                        continue
                    if rc != 0:
                        # No matching process — flag as exited.
                        svc.state = ServiceState.EXITED
                        svc.exited_at = time.time()
                        svc.exit_code = svc.exit_code or 0
                        logger.info(
                            "service.exited",
                            workspace_id=svc.workspace_id,
                            label=svc.label,
                        )
                    if svc.auto_port is None:
                        self._scan_port(svc)
            except Exception:  # noqa: BLE001
                logger.exception("service.reaper_iteration_failed")
            await asyncio.sleep(_REAP_INTERVAL_S)

    def _scan_port(self, svc: Service) -> None:
        try:
            with open(svc.log_path, "rb") as fh:
                blob = fh.read(_PORT_SCAN_BYTES)
        except OSError:
            return
        text = blob.decode("utf-8", errors="replace")
        match = _PORT_PATTERN.search(text)
        if match is None:
            return
        try:
            port = int(match.group(1))
        except ValueError:
            return
        if 1 <= port <= 65535:
            svc.auto_port = port
            if svc.port is None:
                svc.port = port
            logger.info(
                "service.port_detected",
                workspace_id=svc.workspace_id,
                label=svc.label,
                port=port,
            )

    async def list(self, workspace_id: str) -> list[Service]:
        async with self._lock:
            return [s for s in self._entries.values() if s.workspace_id == workspace_id]

    async def get(self, workspace_id: str, label: str) -> Service:
        async with self._lock:
            try:
                return self._entries[(workspace_id, label)]
            except KeyError as exc:
                raise ServiceNotFound(f"{workspace_id}/{label}") from exc

    async def start(
        self,
        *,
        workspace_id: str,
        label: str,
        cmd: str,
        worktree_path: str,
        port: int | None = None,
        env: dict[str, str] | None = None,
    ) -> Service:
        """Spawn `cmd` inside the workspace sandbox. Raises
        `ServiceAlreadyExists` if a service with the same (workspace,
        label) already exists and is still running."""
        if self._sandbox_manager is None:
            raise RuntimeError(
                "service registry has no sandbox manager — wire one via "
                "AppContainer.services before calling start()"
            )
        async with self._lock:
            existing = self._entries.get((workspace_id, label))
            if existing is not None and existing.state in {
                ServiceState.STARTING,
                ServiceState.RUNNING,
            }:
                raise ServiceAlreadyExists(
                    f"service {workspace_id}/{label} is {existing.state.value}"
                )

        # Log file lives on host (under the bind-mounted worktree)
        # *and* shows up inside the container at the same relative
        # path. Truncate so each Start gives a fresh tail.
        log_dir_host = Path(worktree_path) / ".gapt" / "services"
        log_dir_host.mkdir(parents=True, exist_ok=True)
        log_path_host = log_dir_host / f"{label}.log"
        log_path_host.write_bytes(b"")
        log_path_in_container = f"/workspace/.gapt/services/{label}.log"

        # Unique marker for pgrep — gives us a reliable alive-check
        # later (`pgrep -f` matches the full cmdline). Marker is
        # passed as an inert env var so it lands in /proc/<pid>/cmdline.
        marker = f"GAPT_SVC={workspace_id}:{label}"

        service_env = dict(env or {})
        if port is not None and "PORT" not in service_env:
            service_env["PORT"] = str(port)

        # Inner: prepend the marker as an env-set before the user's
        # cmd so pgrep -f hits it. `exec` so the shell hands its PID
        # to the user's process.
        inner_cmd = f"{marker} exec {cmd}"

        sandbox = self._sandbox_manager.get(workspace_id, worktree_path)
        try:
            await sandbox.spawn_background(
                cmd=inner_cmd,
                log_path_inside=log_path_in_container,
                env=service_env,
            )
        except WorkspaceSandboxError as exc:
            raise RuntimeError(f"failed to spawn service {label!r}: {exc}") from exc

        svc = Service(
            workspace_id=workspace_id,
            label=label,
            cmd=cmd,
            worktree_path=worktree_path,
            log_path=str(log_path_host),
            port=port,
            state=ServiceState.RUNNING,
            _pgrep_marker=marker,
        )
        async with self._lock:
            self._entries[(workspace_id, label)] = svc
            self._ensure_reaper()
        logger.info(
            "service.started",
            workspace_id=workspace_id,
            label=label,
            cmd=cmd,
        )
        return svc

    async def stop(
        self,
        workspace_id: str,
        label: str,
        *,
        timeout_s: float = 5.0,
    ) -> Service:
        """SIGTERM the in-container process matching the unique marker,
        then SIGKILL after `timeout_s`. Returns the final state."""
        async with self._lock:
            svc = self._entries.get((workspace_id, label))
        if svc is None:
            raise ServiceNotFound(f"{workspace_id}/{label}")
        if self._sandbox_manager is None:
            return svc
        if svc.state in {ServiceState.EXITED, ServiceState.FAILED, ServiceState.STOPPING}:
            return svc

        svc.state = ServiceState.STOPPING
        sandbox = self._sandbox_manager.get(svc.workspace_id, svc.worktree_path)
        marker = svc._pgrep_marker
        if marker:
            with contextlib.suppress(WorkspaceSandboxError):
                await sandbox.exec(
                    ["sh", "-c", f"pkill -TERM -f {shlex.quote(marker)} || true"],
                    timeout_s=5.0,
                )
            # Brief grace for graceful exit, then force.
            for _ in range(int(timeout_s * 2)):
                with contextlib.suppress(WorkspaceSandboxError):
                    rc, _, _ = await sandbox.exec(
                        ["sh", "-c", f"pgrep -f {shlex.quote(marker)} >/dev/null"],
                        timeout_s=2.0,
                    )
                if rc != 0:
                    break
                await asyncio.sleep(0.5)
            with contextlib.suppress(WorkspaceSandboxError):
                await sandbox.exec(
                    ["sh", "-c", f"pkill -KILL -f {shlex.quote(marker)} || true"],
                    timeout_s=5.0,
                )

        svc.exited_at = time.time()
        svc.state = ServiceState.EXITED if svc.exit_code in (0, None) else ServiceState.FAILED
        logger.info(
            "service.stopped",
            workspace_id=workspace_id,
            label=label,
        )
        return svc

    async def remove(self, workspace_id: str, label: str) -> None:
        """Drop a stopped service from the registry. No-op if still
        running — callers should `stop()` first."""
        async with self._lock:
            svc = self._entries.get((workspace_id, label))
            if svc is None:
                return
            if svc.state in {ServiceState.STARTING, ServiceState.RUNNING}:
                return
            self._entries.pop((workspace_id, label), None)

    async def set_bound(
        self, workspace_id: str, label: str, host: str | None, url: str | None
    ) -> Service:
        """Record (or clear) the Caddy binding so the GET response
        carries the preview URL the user should click."""
        async with self._lock:
            svc = self._entries.get((workspace_id, label))
        if svc is None:
            raise ServiceNotFound(f"{workspace_id}/{label}")
        svc.bound_host = host
        svc.bound_url = url
        return svc

    async def kill_all(self) -> None:
        """Best-effort cleanup at shutdown. Doesn't wait for graceful
        exit beyond the per-stop timeout."""
        async with self._lock:
            keys = list(self._entries.keys())
        for ws, label in keys:
            try:
                await self.stop(ws, label, timeout_s=1.0)
            except Exception:  # noqa: BLE001
                pass

    async def aclose(self) -> None:
        self._closed = True
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(Exception):
                await self._reaper_task
        await self.kill_all()

    def __iter__(self) -> Iterable[Service]:  # type: ignore[override]
        return iter(list(self._entries.values()))
