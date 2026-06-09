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
import re
import shlex
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import structlog

from gapt_server.domains.workspace_sandbox import (
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


def _extract_user_cmd_from_sh_wrapper(line: str) -> str:
    """Pull the user's command out of the `pgrep -af` line for a
    running service wrapper.

    The wrapper shape is roughly:
      `<pid> sh -c 'mkdir -p $(dirname …) && <user-cmd> >> …log 2>&1'`

    `pgrep -f` joins argv with single spaces and drops quoting, so
    by the time we read it the inner cmd already lost its `&&`
    boundaries on disk — but the user-cmd substring is intact
    between `mkdir -p …services) && ` and ` >> /workspace/.gapt/`.
    Slice between those landmarks; if either marker is missing,
    fall back to the whole line so the user at least sees *something*
    rather than an empty cmd."""
    start_marker = ").gapt/services) && "
    # The above mirrors what the spawn_background wrapper writes —
    # `mkdir -p $(dirname …)` resolves to a path under
    # `/workspace/.gapt/services` so this substring is stable.
    end_marker = " >> /workspace/.gapt/services/"
    si = line.find(start_marker)
    if si == -1:
        # Try a looser anchor.
        si = line.find("&& ")
        if si == -1:
            return line.strip()
        si += 3
    else:
        si += len(start_marker)
    ei = line.find(end_marker, si)
    if ei == -1:
        return line[si:].strip()
    return line[si:ei].strip()


def _kill_marker_pgid_script(marker: str, signal: str) -> str:
    """Shell one-liner that signals every process group containing a
    process whose cmdline matches ``marker``.

    Why pgid and not ``pkill -f``: the GAPT spawn wraps the user's
    command in ``sh -c '<inner>' <marker>``. ``pkill -f <marker>``
    only matches that wrapping ``sh`` — the user's command (and its
    grandchildren — ``npm run dev`` → ``node next`` → ``next-server``)
    inherits the sh's pgrp but is invisible to that pkill. Pre-fix
    those descendants kept running as PPID=1 orphans after stop(),
    holding the bound port forever.

    The script:
      1. Resolves all PIDs whose cmdline contains the marker
         (normally exactly one — the wrapping sh).
      2. Reads field 5 of ``/proc/<pid>/stat`` for each — the pgrp id.
      3. Sends ``kill -<signal> -<pgid>`` (negative-PID semantics
         = "signal every process in this group").

    The shell run here is the container's ``/bin/sh`` which on the
    GAPT sandbox images is dash, not bash. dash's builtin ``kill``
    rejects the bash-canonical ``kill -<SIG> -- -<pgid>`` form with
    "Illegal option" — so we omit the ``--`` separator entirely and
    use the bare ``kill -<SIG> -<pgid>`` form that dash and util-
    linux ``kill`` both accept.

    Safety: explicitly skip pgid 0 / 1 so we never signal init even
    if a marker process were mis-attributed. The trailing ``true``
    keeps the exit code at 0 — the caller already wraps this in
    ``contextlib.suppress`` but a clean exit avoids noisy error logs
    when there's simply nothing left to kill (normal idempotent
    second pass).
    """
    quoted = shlex.quote(marker)
    return (
        "for pid in $(pgrep -f " + quoted + " 2>/dev/null); do "
        'pgid=$(awk "{print \\$5}" /proc/$pid/stat 2>/dev/null); '
        '[ -n "$pgid" ] && [ "$pgid" -gt 1 ] && '
        "kill -" + signal + " -$pgid 2>/dev/null; "
        "done; true"
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

    async def recover_from_containers(self, workspaces: list[tuple[str, str]]) -> int:
        """Rebuild the in-memory entry table by scanning each
        workspace's sandbox container for live `GAPT_SVC=…` markers.

        Reason: the registry is in-process state, but the actual
        processes live inside the per-workspace docker container —
        they survive a GAPT server restart. Without this step the
        IDE shows "No services yet" right after a restart even
        though the user's `npm run dev` is happily compiling away.
        Recovery is idempotent: re-running it after a recover only
        repopulates entries that are still alive.

        `workspaces` is a list of `(workspace_id, worktree_path)`
        tuples. Caller supplies them by querying the workspace table
        for rows in `running` status. We can't pull from the DB
        ourselves without a session dependency, and the registry
        intentionally stays DB-agnostic.

        Returns the number of services restored.
        """
        if self._sandbox_manager is None:
            return 0
        restored = 0
        for workspace_id, worktree_path in workspaces:
            sandbox = self._sandbox_manager.get(workspace_id, worktree_path)
            try:
                rc, out, _ = await sandbox.exec(
                    [
                        "sh",
                        "-c",
                        # `pgrep -af GAPT_SVC=` prints `<pid> <cmdline>`
                        # — every running sh wrapper carries the marker
                        # as $0 so this catches them all.
                        "pgrep -af 'GAPT_SVC=' || true",
                    ],
                    timeout_s=5.0,
                )
            except WorkspaceSandboxError:
                # Container missing / not reachable — skip silently;
                # next time the workspace boots the service can be
                # restarted via the wizard or by hand.
                continue
            if rc != 0 and rc != 1:
                continue
            for raw in out.decode("utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line:
                    continue
                # `<pid> sh -c <inner-cmd> GAPT_SVC=<wid>:<label>`
                # cmdline pieces are NUL-separated in /proc but
                # pgrep -f prints them space-joined. Split on the
                # GAPT_SVC marker — everything before is the inner
                # cmd, everything after is workspace_id:label.
                idx = line.find("GAPT_SVC=")
                if idx == -1:
                    continue
                marker_full = line[idx:].strip()
                # marker_full = "GAPT_SVC=<wid>:<label>"
                payload = marker_full[len("GAPT_SVC="):]
                if ":" not in payload:
                    continue
                wid_in_marker, label = payload.split(":", 1)
                if wid_in_marker != workspace_id:
                    # Marker collision shouldn't happen, but if it
                    # does we trust the workspace_id we're scanning.
                    continue
                # Extract the user's cmd from the sh -c wrapper.
                # Pattern: `sh -c <inner> GAPT_SVC=...`
                inner = line[:idx].strip()
                # Drop the `<pid> ` prefix and the leading `sh -c `.
                # We only need a best-effort cmd for display + restart.
                user_cmd = _extract_user_cmd_from_sh_wrapper(inner)

                key = (workspace_id, label)
                if key in self._entries:
                    # Already known (e.g. user clicked restart between
                    # boot and recover) — leave it alone.
                    continue
                log_path_host = (
                    Path(worktree_path) / ".gapt" / "services" / f"{label}.log"
                )
                self._entries[key] = Service(
                    workspace_id=workspace_id,
                    label=label,
                    cmd=user_cmd,
                    worktree_path=worktree_path,
                    log_path=str(log_path_host),
                    state=ServiceState.RUNNING,
                    _pgrep_marker=marker_full,
                )
                restored += 1
                logger.info(
                    "service.recovered",
                    workspace_id=workspace_id,
                    label=label,
                )
        if restored:
            self._ensure_reaper()
        return restored

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
            except Exception:
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
        # later (`pgrep -f` matches the full cmdline). The sandbox
        # plants it as $0 on the wrapping `sh` so /proc/PID/cmdline
        # carries it without polluting the user's cmd.
        marker = f"GAPT_SVC={workspace_id}:{label}"

        service_env = dict(env or {})
        if port is not None and "PORT" not in service_env:
            service_env["PORT"] = str(port)
        # File-watcher polling — bind-mounted worktrees over docker's
        # overlay sometimes drop inotify events, especially on Linux
        # hosts with cgroup v2 or remote-fs storage. The frameworks
        # below honour these env vars (or one of them, depending on
        # version) and fall back to scheduled re-scans. Setting them
        # unconditionally is harmless — non-watcher processes ignore
        # unknown env. Caller can override by passing their own
        # values in `env=`.
        for k, v in (
            ("CHOKIDAR_USEPOLLING", "1"),   # nodemon, ts-node-dev, vite older versions
            ("WATCHPACK_POLLING", "true"),  # webpack-dev-server, next dev (webpack)
            ("WATCHPACK_POLLING_INTERVAL", "500"),
            ("NEXT_WEBPACK_USEPOLLING", "1"),  # explicit Next.js hook
        ):
            service_env.setdefault(k, v)

        sandbox = self._sandbox_manager.get(workspace_id, worktree_path)
        try:
            await sandbox.spawn_background(
                cmd=cmd,
                log_path_inside=log_path_in_container,
                env=service_env,
                marker=marker,
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
        """SIGTERM the in-container process tree rooted at the marker,
        then SIGKILL after `timeout_s`. Returns the final state.

        Phase N.3 — pgid-based kill. Pre-fix this method ran
        ``pkill -TERM -f <marker>`` which matched only the wrapping
        ``sh`` that carries the marker as ``$0``. The user's actual
        command (``npm run dev`` → ``node next`` → ``next-server``)
        spawned beneath that sh inherits its pgrp but is NOT pgrep-
        matched by the marker pattern. So killing the marker left
        every descendant alive as PPID=1 orphans (docker-init reaps
        them), and they kept holding the bound port — every
        subsequent ``start`` then failed with EADDRINUSE and the user
        was stuck.

        By targeting the marker's pgid (``kill -- -<pgid>``) every
        process that inherited the group goes down in one syscall.
        The only escapees are processes that explicitly called
        ``setpgid(0,0)`` to break out — uncommon for dev servers."""
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
                    ["sh", "-c", _kill_marker_pgid_script(marker, "TERM")],
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
                    ["sh", "-c", _kill_marker_pgid_script(marker, "KILL")],
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
            except Exception:
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
