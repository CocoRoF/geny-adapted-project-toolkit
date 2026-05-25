"""In-process registry of managed background services.

Each `Service` wraps a spawned subprocess whose stdout/stderr are
appended to a log file in the worktree. The registry is keyed by
(workspace_id, label) so two workspaces can both have a `web`
service without collision.

Concurrency model:
- `start()` is async-safe via the registry's lock.
- The reaper task polls `proc.poll()` every 500 ms — light enough
  to ignore, fast enough that the UI's 2 s status poll always sees
  a fresh state.
- `expose()` is a *binding* concern (Caddy admin) and is handled by
  the router on top of this primitive — the registry just records
  the bound URL so the GET response carries it.

The `auto_port` scanner watches the log tail; once a recognised
pattern lands the service's `port` field is filled in even if the
user didn't specify one at start-time.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import signal
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import structlog

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
    """A single managed process. Mutable — the registry owns the
    only reference, callers get snapshots via `Service.snapshot()`."""

    workspace_id: str
    label: str
    cmd: str  # full command line; split via shlex at spawn
    worktree_path: str
    log_path: str
    port: int | None = None
    pid: int | None = None
    state: ServiceState = ServiceState.STARTING
    started_at: float = field(default_factory=time.time)
    exited_at: float | None = None
    exit_code: int | None = None
    bound_url: str | None = None  # set when expose() succeeds
    bound_host: str | None = None
    auto_port: int | None = None  # what the scanner found
    _proc: asyncio.subprocess.Process | None = None

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

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], Service] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None
        self._closed = False

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
                    if svc._proc is None:
                        continue
                    rc = svc._proc.returncode
                    if rc is None:
                        continue
                    # Process has exited but we haven't recorded it.
                    if svc.state not in {ServiceState.EXITED, ServiceState.FAILED}:
                        svc.exit_code = rc
                        svc.exited_at = time.time()
                        svc.state = ServiceState.EXITED if rc == 0 else ServiceState.FAILED
                        logger.info(
                            "service.exited",
                            workspace_id=svc.workspace_id,
                            label=svc.label,
                            exit_code=rc,
                        )
                    # Auto-port scan: only if we still don't know it.
                    if svc.auto_port is None:
                        self._scan_port(svc)
                # Also try to scan ports for running services that
                # haven't filled in yet.
                for svc in services:
                    if svc.state == ServiceState.RUNNING and svc.auto_port is None:
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
        """Spawn a new service. Raises `ServiceAlreadyExists` if a
        service with the same (workspace, label) already exists and
        is still running. Exited services with the same key are
        replaced (so "restart" boils down to stop + start)."""
        async with self._lock:
            existing = self._entries.get((workspace_id, label))
            if existing is not None and existing.state in {
                ServiceState.STARTING,
                ServiceState.RUNNING,
            }:
                raise ServiceAlreadyExists(
                    f"service {workspace_id}/{label} is {existing.state.value}"
                )

        # Prepare the log file (truncate so the user sees a fresh start
        # — the rotation strategy lives in the file-tail SSE client).
        log_dir = Path(worktree_path) / ".gapt" / "services"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{label}.log"
        log_fd = os.open(
            str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644
        )

        # Build env: inherit + caller overrides + PORT hint when given
        # (lots of dev servers honor `PORT`).
        proc_env = dict(os.environ)
        if env:
            proc_env.update(env)
        if port is not None and "PORT" not in proc_env:
            proc_env["PORT"] = str(port)

        try:
            argv = shlex.split(cmd)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                cwd=worktree_path,
                env=proc_env,
                start_new_session=True,  # process-group leader so SIGTERM hits children
                close_fds=True,
            )
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            os.close(log_fd)
            raise RuntimeError(f"failed to spawn service {label!r}: {exc}") from exc
        finally:
            os.close(log_fd)  # parent doesn't need to write — subprocess owns the dup

        svc = Service(
            workspace_id=workspace_id,
            label=label,
            cmd=cmd,
            worktree_path=worktree_path,
            log_path=str(log_path),
            port=port,
            pid=proc.pid,
            state=ServiceState.RUNNING,
            _proc=proc,
        )
        async with self._lock:
            self._entries[(workspace_id, label)] = svc
            self._ensure_reaper()
        logger.info(
            "service.started",
            workspace_id=workspace_id,
            label=label,
            pid=proc.pid,
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
        """SIGTERM the process group, then SIGKILL after `timeout_s`.
        Returns the final service state."""
        async with self._lock:
            svc = self._entries.get((workspace_id, label))
        if svc is None:
            raise ServiceNotFound(f"{workspace_id}/{label}")
        if svc._proc is None or svc._proc.returncode is not None:
            return svc

        svc.state = ServiceState.STOPPING
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(svc._proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(svc._proc.wait(), timeout=timeout_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(svc._proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                await svc._proc.wait()

        svc.exit_code = svc._proc.returncode if svc._proc else None
        svc.exited_at = time.time()
        svc.state = ServiceState.EXITED if svc.exit_code == 0 else ServiceState.FAILED
        logger.info(
            "service.stopped",
            workspace_id=workspace_id,
            label=label,
            exit_code=svc.exit_code,
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
