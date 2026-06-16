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

from gapt_server.domains.services.port_reconcile import (
    VALID_PORT_POLICIES,
    extract_log_port_hint,
    free_listener_pgid_script,
    parse_intended_port,
)
from gapt_server.domains.workspace_sandbox import (
    WorkspaceSandbox,
    WorkspaceSandboxError,
    WorkspaceSandboxManager,
)

logger = structlog.get_logger(__name__)


# Capture loop's poll cadence — service state transitions surface to
# the UI at the *max* of this and the UI's own poll interval.
_REAP_INTERVAL_S = 0.5

# Auto-port: scan the TAIL of the log output. The tail (not the head)
# matters because `npm install && npm run dev`-style commands can push
# the server banner past any fixed head window, and a dev server that
# rebinds (port drift: 5173 in use → 5174) prints the *current* port
# last.
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
    cmd = line[si:].strip() if ei == -1 else line[si:ei].strip()
    # The spawn wrapper groups the user cmd (`{ cmd ; }`) so the log
    # redirect covers the whole chain — unwrap it for display/restart.
    if cmd.startswith("{") and cmd.endswith("}"):
        cmd = cmd[1:-1].strip()
        if cmd.endswith(";"):
            cmd = cmd[:-1].strip()
    return cmd


def _pgrep_pattern(marker: str) -> str:
    """Non-self-matching pgrep regex for ``marker``.

    The classic bracket trick: ``GAPT_SVC=…`` becomes ``[G]APT_SVC=…``.
    The probing/killing ``sh -c`` carries the *pattern* in its own
    cmdline — which contains the literal ``[G]APT_SVC=`` and therefore
    does NOT match the regex — while the real service wrapper carries
    the plain marker as ``$0`` and matches. Without this, every
    alive-check matched the checker itself (rc always 0): the reaper
    never flipped dead services to EXITED, stop() always burned its
    full grace timeout, and the kill script could signal its own
    process group."""
    if not marker:
        return marker
    return f"[{marker[0]}]{marker[1:]}"


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

    The pgrep pattern is the bracketed non-self-matching form (see
    ``_pgrep_pattern``) — otherwise this very ``sh -c`` matches its
    own cmdline, resolves its OWN pgid and kills itself, potentially
    before the real target is signalled.
    """
    quoted = shlex.quote(_pgrep_pattern(marker))
    return (
        "for pid in $(pgrep -f " + quoted + " 2>/dev/null); do "
        'pgid=$(awk "{print \\$5}" /proc/$pid/stat 2>/dev/null); '
        '[ -n "$pgid" ] && [ "$pgid" -gt 1 ] && '
        "kill -" + signal + " -$pgid 2>/dev/null; "
        "done; true"
    )


# ───────────────────────── loopback forwarder ─────────────────────
#
# Dev servers commonly bind loopback only (vite's default is
# localhost; `npm run dev` users rarely know about `--host`). The
# preview chain dials the container over the docker network
# (`gapt-ws-<wid>:<port>`), so a loopback-bound server 502s even
# though everything else is wired correctly. Instead of bouncing the
# user with "edit your vite config", expose() transparently runs a
# tiny TCP forwarder inside the container: it binds the container's
# eth0 address on the same port (loopback binds don't conflict with a
# specific-address bind) and pipes to 127.0.0.1/::1 where the app
# actually listens. Pure stdlib — python3 preferred, node fallback —
# so it works on any dev image. Lifecycle is tied to the service via
# the GAPT_FWD marker: stop()/restart kill it alongside the app.

_FORWARD_PY = """\
import socket, sys, threading

port = int(sys.argv[1])
target = sys.argv[2]
bind = socket.gethostbyname(socket.gethostname())
fam = socket.AF_INET6 if ":" in target else socket.AF_INET

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((bind, port))
srv.listen(64)


def pump(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


while True:
    c, _ = srv.accept()
    try:
        u = socket.socket(fam, socket.SOCK_STREAM)
        u.connect((target, port))
    except OSError:
        c.close()
        continue
    threading.Thread(target=pump, args=(c, u), daemon=True).start()
    threading.Thread(target=pump, args=(u, c), daemon=True).start()
"""

_FORWARD_CJS = """\
const net = require("net");
const os = require("os");

const port = Number(process.argv[2]);
const target = process.argv[3];

let bind = "0.0.0.0";
for (const addrs of Object.values(os.networkInterfaces())) {
  for (const a of addrs || []) {
    if (a.family === "IPv4" && !a.internal) bind = a.address;
  }
}

net
  .createServer((c) => {
    const u = net.connect({ host: target, port });
    u.on("error", () => c.destroy());
    c.on("error", () => u.destroy());
    u.on("close", () => c.destroy());
    c.on("close", () => u.destroy());
    c.pipe(u);
    u.pipe(c);
  })
  .listen(port, bind);
"""


def _parse_listeners(tcp4: str, tcp6: str, port: int) -> dict[str, bool]:
    """Classify the in-container LISTEN sockets for ``port`` from
    `/proc/net/tcp` + `/proc/net/tcp6` content.

    Returns flags: ``any`` (some listener exists), ``reachable``
    (at least one listener accepts non-loopback traffic — wildcard or
    a specific non-loopback address), ``lo4`` / ``lo6`` (which
    loopback family is listening — picks the forwarder's dial
    target)."""
    want = f"{port:04X}"
    flags = {"any": False, "reachable": False, "lo4": False, "lo6": False}

    def _scan(text: str, v6: bool) -> None:
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            local, state = parts[1], parts[3]
            if state.upper() != "0A":  # TCP_LISTEN
                continue
            addr_hex, _, port_hex = local.partition(":")
            if port_hex.upper() != want:
                continue
            flags["any"] = True
            a = addr_hex.upper()
            if v6:
                if a == "0" * 32:
                    flags["reachable"] = True  # [::] wildcard
                elif a == "00000000000000000000000001000000":
                    flags["lo6"] = True  # [::1]
                elif a.startswith("0000000000000000FFFF0000"):
                    # IPv4-mapped — last 8 hex chars are the v4 addr
                    # little-endian; 0100007F = 127.0.0.1.
                    if a.endswith("0100007F"):
                        flags["lo4"] = True
                    else:
                        flags["reachable"] = True
                else:
                    flags["reachable"] = True
            elif a == "00000000":
                flags["reachable"] = True  # 0.0.0.0 wildcard
            elif a == "0100007F":
                flags["lo4"] = True  # 127.0.0.1
            else:
                flags["reachable"] = True  # eth0-specific bind

    _scan(tcp4, v6=False)
    _scan(tcp6, v6=True)
    return flags


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


class ServicePortConflict(RuntimeError):
    """Raised (under ``port_conflict_policy="strict"``) when the port a
    service is about to bind is held by a foreign process the registry
    declined to reap. Carries the port so the router can render a
    structured ``service.port_conflict`` error."""

    def __init__(self, port: int) -> None:
        self.port = port
        super().__init__(
            f"port {port} is already in use by another process and "
            "ports_policy=strict forbids reaping it"
        )


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
    # The port the USER declared at start (None = auto-detect). Kept
    # separate from `port` because `port` gets auto-promoted from the
    # log scanner — restart must re-run with the user's intent, not a
    # previous run's detection (which manufactures port drift).
    user_port: int | None = None
    # User-supplied env from start() — restart replays it. Without
    # this a restart silently dropped DATABASE_URL-style settings.
    env: dict[str, str] = field(default_factory=dict)
    # Log size at the last port scan — lets the scanner re-run only
    # when the file has grown (cheap stat per reaper tick).
    _scanned_size: int = -1
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

    def __init__(
        self,
        sandbox_manager: WorkspaceSandboxManager | None = None,
        *,
        port_conflict_policy: str = "free",
    ) -> None:
        self._entries: dict[tuple[str, str], Service] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None
        self._closed = False
        # Injected at container build-time so the registry can spawn
        # in-sandbox commands. Tests can omit it (or pass a stub) —
        # `start()` raises a clear error if it's None and a real
        # spawn is attempted.
        self._sandbox_manager = sandbox_manager
        # How start() reconciles a port that's already held inside the
        # container — "free" (default: reap the stale holder so the
        # restart succeeds), "strict" (refuse and surface the conflict),
        # or "off" (skip the port-free step). An unknown value degrades
        # to "free" rather than wedging every service start.
        self._port_policy = (
            port_conflict_policy if port_conflict_policy in VALID_PORT_POLICIES else "free"
        )

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
                        # `pgrep -af` prints `<pid> <cmdline>` — every
                        # running sh wrapper carries the marker as $0
                        # so this catches them all. Bracketed pattern
                        # so the probing sh doesn't match itself.
                        "pgrep -af '[G]APT_SVC=' || true",
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
                payload = marker_full[len("GAPT_SVC=") :]
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
                log_path_host = Path(worktree_path) / ".gapt" / "services" / f"{label}.log"
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
                        self._scan_port(svc)
                        continue
                    if self._sandbox_manager is None:
                        continue
                    # pgrep against the container to see if the
                    # process is still alive. -f matches the full
                    # cmdline so our unique marker hits. The bracketed
                    # pattern keeps the probing sh from matching its
                    # own cmdline (which would always report "alive").
                    sandbox = self._sandbox_manager.get(svc.workspace_id, svc.worktree_path)
                    pattern = _pgrep_pattern(svc._pgrep_marker)
                    try:
                        rc, _, _ = await sandbox.exec(
                            ["sh", "-c", f"pgrep -f {shlex.quote(pattern)} >/dev/null"],
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
                    self._scan_port(svc)
            except Exception:
                logger.exception("service.reaper_iteration_failed")
            await asyncio.sleep(_REAP_INTERVAL_S)

    def _scan_port(self, svc: Service) -> None:
        """Scan the log TAIL for the most recent "listening on :PORT"
        line. Re-runs whenever the file grows (cheap stat gate), and
        takes the LAST match — a dev server that found its declared
        port taken (vite: 5173 in use → 5174) prints the real port
        after the conflict notice, and a mid-life rebind must update
        the detection rather than being ignored forever."""
        try:
            size = os.path.getsize(svc.log_path)
        except OSError:
            return
        if size == svc._scanned_size:
            return
        try:
            with open(svc.log_path, "rb") as fh:
                if size > _PORT_SCAN_BYTES:
                    fh.seek(size - _PORT_SCAN_BYTES)
                blob = fh.read(_PORT_SCAN_BYTES)
        except OSError:
            return
        svc._scanned_size = size
        text = blob.decode("utf-8", errors="replace")
        matches = list(_PORT_PATTERN.finditer(text))
        if not matches:
            return
        try:
            port = int(matches[-1].group(1))
        except ValueError:
            return
        if 1 <= port <= 65535 and port != svc.auto_port:
            svc.auto_port = port
            if svc.user_port is None:
                # No declared port — track the detection so restart /
                # expose follow the live value.
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
        marker = f"GAPT_SVC={workspace_id}:{label}"
        # Reserve the slot under the SAME lock as the existence check —
        # otherwise two concurrent start()s both pass the check, both
        # spawn, and one process leaks (TOCTOU). The placeholder makes
        # the loser see an in-progress entry and raise immediately. On
        # any failure below we remove it so a failed start is retryable.
        async with self._lock:
            existing = self._entries.get((workspace_id, label))
            if existing is not None and existing.state in {
                ServiceState.STARTING,
                ServiceState.RUNNING,
            }:
                raise ServiceAlreadyExists(
                    f"service {workspace_id}/{label} is {existing.state.value}"
                )
            placeholder = Service(
                workspace_id=workspace_id,
                label=label,
                cmd=cmd,
                worktree_path=worktree_path,
                log_path="",
                state=ServiceState.STARTING,
                _pgrep_marker=marker,
            )
            self._entries[(workspace_id, label)] = placeholder

        # Log file lives on host (under the bind-mounted worktree)
        # *and* shows up inside the container at the same relative
        # path. Truncate so each Start gives a fresh tail.
        log_dir_host = Path(worktree_path) / ".gapt" / "services"
        log_dir_host.mkdir(parents=True, exist_ok=True)
        log_path_host = log_dir_host / f"{label}.log"
        # Before truncating, recover the port the PREVIOUS run bound from
        # its log — the only place it survives when the command hides the
        # port in a package.json script (`npm run dev` → `next dev
        # -p 3000`) and the live cmd string carries nothing for the
        # parser. Used by the port reconcile below to free a zombie.
        prev_log_hint: int | None = None
        try:
            tail = log_path_host.read_bytes()[-_PORT_SCAN_BYTES:]
            prev_log_hint = extract_log_port_hint(tail.decode("utf-8", errors="replace"))
        except OSError:
            prev_log_hint = None
        log_path_host.write_bytes(b"")
        log_path_in_container = f"/workspace/.gapt/services/{label}.log"

        # Unique marker for pgrep — gives us a reliable alive-check
        # later (`pgrep -f` matches the full cmdline). The sandbox
        # plants it as $0 on the wrapping `sh` so /proc/PID/cmdline
        # carries it without polluting the user's cmd. (Marker computed
        # above, before the slot reservation.)

        service_env = dict(env or {})
        if port is not None and "PORT" not in service_env:
            service_env["PORT"] = str(port)
        # Bind on all interfaces by default. The preview chain dials
        # the container over the docker network (`gapt-ws-<wid>:<port>`)
        # — a dev server that binds loopback only is unreachable from
        # Caddy and 502s. CRA / Nuxt / webpack-dev-server / many
        # uvicorn setups honour HOST; vite ignores env (the expose
        # path covers it with a loopback forwarder instead).
        service_env.setdefault("HOST", "0.0.0.0")
        # File-watcher polling — bind-mounted worktrees over docker's
        # overlay sometimes drop inotify events, especially on Linux
        # hosts with cgroup v2 or remote-fs storage. The frameworks
        # below honour these env vars (or one of them, depending on
        # version) and fall back to scheduled re-scans. Setting them
        # unconditionally is harmless — non-watcher processes ignore
        # unknown env. Caller can override by passing their own
        # values in `env=`.
        for k, v in (
            ("CHOKIDAR_USEPOLLING", "1"),  # nodemon, ts-node-dev, vite older versions
            ("WATCHPACK_POLLING", "true"),  # webpack-dev-server, next dev (webpack)
            ("WATCHPACK_POLLING_INTERVAL", "500"),
            ("NEXT_WEBPACK_USEPOLLING", "1"),  # explicit Next.js hook
        ):
            service_env.setdefault(k, v)

        sandbox = self._sandbox_manager.get(workspace_id, worktree_path)

        # Port reconcile — the dev-service analog of the prod deploy
        # port preflight. Reap a stale previous instance and free the
        # port this service is about to bind, so a restart after a GAPT
        # restart/crash doesn't die with EADDRINUSE. The intended port
        # comes from the command, else the previous run's log.
        intended_port = parse_intended_port(cmd, env, port) or prev_log_hint
        try:
            reconcile_status = await self._reconcile_port(
                workspace_id=workspace_id,
                label=label,
                worktree_path=worktree_path,
                intended_port=intended_port,
            )
        except ServicePortConflict:
            # strict policy + foreign holder — release the slot and let
            # the router surface a structured 409.
            async with self._lock:
                if self._entries.get((workspace_id, label)) is placeholder:
                    del self._entries[(workspace_id, label)]
            raise
        if reconcile_status not in ("", "noop", "marker_reap", "clear"):
            logger.info(
                "service.port_reconciled",
                workspace_id=workspace_id,
                label=label,
                port=intended_port,
                status=reconcile_status,
            )

        try:
            await sandbox.spawn_background(
                cmd=cmd,
                log_path_inside=log_path_in_container,
                env=service_env,
                marker=marker,
            )
        except WorkspaceSandboxError as exc:
            # Release the reserved slot so the failed start is retryable
            # instead of wedging on a STARTING placeholder forever.
            async with self._lock:
                if self._entries.get((workspace_id, label)) is placeholder:
                    del self._entries[(workspace_id, label)]
            raise RuntimeError(f"failed to spawn service {label!r}: {exc}") from exc

        svc = Service(
            workspace_id=workspace_id,
            label=label,
            cmd=cmd,
            worktree_path=worktree_path,
            log_path=str(log_path_host),
            port=port,
            user_port=port,
            env=dict(env or {}),
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

    async def _reconcile_port(
        self,
        *,
        workspace_id: str,
        label: str,
        worktree_path: str,
        intended_port: int | None,
    ) -> str:
        """Free the port a service is about to bind so a (re)start
        doesn't die with EADDRINUSE — the dev-service counterpart of the
        prod deploy port preflight.

        Two independent steps:

        1. Always reap a *previous instance of this exact service* — its
           ``GAPT_SVC=<wid>:<label>`` wrapper and any ``GAPT_FWD=``
           loopback forwarder. Those markers survive a GAPT
           restart/crash, and stop()'s pgid kill can miss a descendant
           that called ``setpgid()`` (Turbopack's detached
           ``next-server`` is the usual culprit). It's always our own
           leftover, so it's reaped unconditionally, regardless of
           policy.

        2. If the intended port is known and *still* held after step 1
           — i.e. by a process with no GAPT marker (the detached
           grandchild, or a genuinely foreign listener) — apply the
           policy: ``free`` reaps whatever holds it; ``strict`` raises
           ``ServicePortConflict``; ``off`` leaves it (the dev server
           reports the conflict itself).

        Never raises except the deliberate strict-policy conflict — a
        sandbox hiccup degrades to best-effort so diagnostics can't
        block a start. Returns a short status string for the log line.
        """
        if self._sandbox_manager is None:
            return "noop"
        sandbox = self._sandbox_manager.get(workspace_id, worktree_path)

        # Step 1 — reap our own previous instance (a no-op on a
        # first-ever start: nothing matches the markers).
        for mk in (
            f"GAPT_SVC={workspace_id}:{label}",
            f"GAPT_FWD={workspace_id}:{label}",
        ):
            with contextlib.suppress(WorkspaceSandboxError):
                await sandbox.exec(
                    ["sh", "-c", _kill_marker_pgid_script(mk, "KILL")],
                    timeout_s=5.0,
                )

        if intended_port is None or self._port_policy == "off":
            return "marker_reap"

        # Give the just-killed tree a beat to release the socket before
        # we judge the port held by something else.
        await asyncio.sleep(0.2)
        if not await self._port_in_use(sandbox, intended_port):
            return "clear"

        if self._port_policy == "strict":
            raise ServicePortConflict(intended_port)

        # "free" — reap the foreign holder: TERM, brief grace, then
        # KILL, polling the container's LISTEN table until it frees.
        for signal in ("TERM", "KILL"):
            with contextlib.suppress(WorkspaceSandboxError):
                await sandbox.exec(
                    ["sh", "-c", free_listener_pgid_script(intended_port, signal)],
                    timeout_s=8.0,
                )
            for _ in range(6):
                await asyncio.sleep(0.3)
                if not await self._port_in_use(sandbox, intended_port):
                    return "freed"
        # Couldn't free it (a foreign un-killable process?). Proceed and
        # let the dev server surface the conflict — but flag it so the
        # operator can see we tried.
        logger.warning(
            "service.port_still_held",
            workspace_id=workspace_id,
            label=label,
            port=intended_port,
        )
        return "still_held"

    async def _port_in_use(self, sandbox: WorkspaceSandbox, port: int) -> bool:
        """True if any process inside the container LISTENs on ``port``.
        Reuses the ``/proc/net`` parser; a sandbox error reads as 'free'
        so diagnostics never block a start."""
        try:
            _, out, _ = await sandbox.exec(
                [
                    "sh",
                    "-c",
                    "cat /proc/net/tcp 2>/dev/null; echo ---; cat /proc/net/tcp6 2>/dev/null",
                ],
                timeout_s=5.0,
            )
        except WorkspaceSandboxError:
            return False
        text = out.decode("utf-8", errors="replace")
        tcp4, _, tcp6 = text.partition("---")
        return _parse_listeners(tcp4, tcp6, port)["any"]

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
        still_alive = False
        if marker:
            pattern = shlex.quote(_pgrep_pattern(marker))
            with contextlib.suppress(WorkspaceSandboxError):
                await sandbox.exec(
                    ["sh", "-c", _kill_marker_pgid_script(marker, "TERM")],
                    timeout_s=5.0,
                )
            # Brief grace for graceful exit, then force. `rc` defaults
            # to 1 ("gone") each round so a sandbox exec failure can't
            # leave it unbound (NameError) or stuck on a stale value.
            for _ in range(int(timeout_s * 2)):
                rc = 1
                with contextlib.suppress(WorkspaceSandboxError):
                    rc, _, _ = await sandbox.exec(
                        ["sh", "-c", f"pgrep -f {pattern} >/dev/null"],
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
            # Verify the tree actually died. SIGKILL is not ignorable
            # but delivery is async — give it a beat, then re-check.
            # A survivor means an orphan still holds the port; report
            # FAILED instead of silently claiming EXITED, so restart /
            # the operator can see something is wrong instead of the
            # next start drifting to port+1.
            await asyncio.sleep(0.2)
            with contextlib.suppress(WorkspaceSandboxError):
                rc2, _, _ = await sandbox.exec(
                    ["sh", "-c", f"pgrep -f {pattern} >/dev/null"],
                    timeout_s=2.0,
                )
                still_alive = rc2 == 0
        # Take down the loopback forwarder (if expose spawned one) —
        # it holds the same port on the container interface.
        with contextlib.suppress(WorkspaceSandboxError):
            await sandbox.exec(
                [
                    "sh",
                    "-c",
                    _kill_marker_pgid_script(f"GAPT_FWD={svc.workspace_id}:{svc.label}", "KILL"),
                ],
                timeout_s=5.0,
            )

        svc.exited_at = time.time()
        if still_alive:
            svc.state = ServiceState.FAILED
            logger.warning(
                "service.stop_incomplete",
                workspace_id=workspace_id,
                label=label,
                reason="process tree survived SIGKILL pass — port may stay held",
            )
        else:
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

    async def ensure_reachable(
        self,
        *,
        workspace_id: str,
        worktree_path: str,
        label: str,
        port: int,
    ) -> str:
        """Make ``port`` reachable from the docker network before a
        Caddy route is pointed at it.

        Looks at the container's actual LISTEN sockets:

        - already reachable (wildcard / non-loopback bind) → "reachable"
        - loopback-only (vite default!) → spawn the in-container
          forwarder and verify it came up → "forwarded"
        - nothing listening yet (still booting) → "unknown" — caller
          proceeds; the warm-up request covers the race
        - loopback-only and the forwarder can't run (no python3/node)
          → "unreachable" — caller should surface a structured hint

        Never raises: any sandbox hiccup degrades to "unknown" so an
        expose attempt is not blocked by diagnostics."""
        if self._sandbox_manager is None:
            return "unknown"
        sandbox = self._sandbox_manager.get(workspace_id, worktree_path)
        try:
            _, out, _ = await sandbox.exec(
                [
                    "sh",
                    "-c",
                    "cat /proc/net/tcp 2>/dev/null; echo ---; cat /proc/net/tcp6 2>/dev/null",
                ],
                timeout_s=5.0,
            )
        except WorkspaceSandboxError:
            return "unknown"
        text = out.decode("utf-8", errors="replace")
        tcp4, _, tcp6 = text.partition("---")
        flags = _parse_listeners(tcp4, tcp6, port)
        if flags["reachable"]:
            return "reachable"
        if not flags["any"]:
            return "unknown"

        # Loopback-only. Drop the forwarder sources into the worktree
        # (bind-mounted, so writing host-side is enough) and spawn it
        # with its own marker so stop()/restart can reap it.
        target = "127.0.0.1" if flags["lo4"] else "::1"
        fwd_dir = Path(worktree_path) / ".gapt"
        try:
            fwd_dir.mkdir(parents=True, exist_ok=True)
            (fwd_dir / "forward.py").write_text(_FORWARD_PY, encoding="utf-8")
            (fwd_dir / "forward.cjs").write_text(_FORWARD_CJS, encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "service.forwarder.write_failed",
                workspace_id=workspace_id,
                label=label,
                error=str(exc),
            )
            return "unreachable"
        marker = f"GAPT_FWD={workspace_id}:{label}"
        # Replace any previous forwarder for this service (port may
        # have changed across restarts).
        with contextlib.suppress(WorkspaceSandboxError):
            await sandbox.exec(
                ["sh", "-c", _kill_marker_pgid_script(marker, "KILL")],
                timeout_s=5.0,
            )
        runner = (
            "if command -v python3 >/dev/null 2>&1; then "
            f"python3 /workspace/.gapt/forward.py {port} {target}; "
            "elif command -v node >/dev/null 2>&1; then "
            f"node /workspace/.gapt/forward.cjs {port} {target}; "
            "else echo 'gapt: no python3/node available for the loopback forwarder' >&2; exit 9; fi"
        )
        try:
            await sandbox.spawn_background(
                cmd=runner,
                log_path_inside=f"/workspace/.gapt/services/{label}.fwd.log",
                marker=marker,
            )
        except WorkspaceSandboxError as exc:
            logger.warning(
                "service.forwarder.spawn_failed",
                workspace_id=workspace_id,
                label=label,
                error=str(exc),
            )
            return "unreachable"
        # Verify the forwarder is actually accepting on the container
        # address — a few quick probes (it binds within milliseconds;
        # the retry covers slower images).
        check = (
            f'curl -s -o /dev/null --max-time 2 "http://$(hostname):{port}/" '
            f'|| wget -q -T 2 -O /dev/null "http://$(hostname):{port}/"'
        )
        for _ in range(5):
            await asyncio.sleep(0.3)
            with contextlib.suppress(WorkspaceSandboxError):
                rc, _, _ = await sandbox.exec(["sh", "-c", check], timeout_s=6.0)
                if rc == 0:
                    logger.info(
                        "service.forwarder.active",
                        workspace_id=workspace_id,
                        label=label,
                        port=port,
                        target=target,
                    )
                    return "forwarded"
        logger.warning(
            "service.forwarder.unverified",
            workspace_id=workspace_id,
            label=label,
            port=port,
        )
        # Spawned but not verified — likely missing curl/wget rather
        # than a dead forwarder. Treat as forwarded (best-effort).
        return "forwarded"

    async def kill_forwarder(self, *, workspace_id: str, worktree_path: str, label: str) -> None:
        """Take down the loopback forwarder for a service (unexpose)."""
        if self._sandbox_manager is None:
            return
        sandbox = self._sandbox_manager.get(workspace_id, worktree_path)
        with contextlib.suppress(WorkspaceSandboxError):
            await sandbox.exec(
                [
                    "sh",
                    "-c",
                    _kill_marker_pgid_script(f"GAPT_FWD={workspace_id}:{label}", "KILL"),
                ],
                timeout_s=5.0,
            )

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
            # Awaiting a cancelled task re-raises CancelledError, which
            # is BaseException (not Exception) on 3.8+ — suppress(Exception)
            # alone would let it escape aclose() and break the lifespan
            # shutdown / any caller that closes a registry with a live
            # reaper. Suppress both it and a real exception the reaper
            # may have stored; we're tearing down regardless.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reaper_task
        await self.kill_all()

    def __iter__(self) -> Iterable[Service]:  # type: ignore[override]
        return iter(list(self._entries.values()))
