"""WorkspaceSandbox — per-workspace Docker container.

Single container per workspace. All commands the user (or the agent)
issues from GAPT — terminal keystrokes, dev server spawns, agent
file ops, anything via `SandboxBackend.exec_in` — go through
`docker exec` into this container. Inside, only the bind-mounted
worktree at `/workspace` is reachable. No host fs leak.

Lifecycle
---------
- *Lazy ensure*: `manager.ensure(workspace_id, worktree_path)` checks
  for an existing container by name; starts one if missing. Cheap
  enough to call on every command — first call costs ~300 ms,
  subsequent calls cost ~5 ms.
- *Stop*: `manager.stop(workspace_id)` removes the container. Called
  from the workspace delete endpoint + app shutdown.
- *Image*: configurable. Default `ubuntu:24.04` because it ships
  bash + apt, the minimum the user needs to bootstrap any stack with
  one `apt install`. Projects with heavy native deps should build
  their own image and set `GAPT_WORKSPACE_SANDBOX_IMAGE`.
- *Cleanup at process exit*: `manager.aclose()` SIGKILLs every
  container the manager created. Containers are named
  `gapt-ws-<wid>` so a manual `docker ps` shows them clearly.

What this is NOT
----------------
- *Not* full Sysbox — there is no inner dockerd, no init system, no
  systemd. Apps that fork worker processes work fine, apps that
  expect a real init don't. For dev that's correct.
- *Not* network-isolated — we use the default bridge so dev servers
  inside the container can hit npm/pip/etc registries directly.
  Outbound restriction lands when prod runs Sysbox proper.

Threat model
------------
- User accidentally typing `rm -rf /` in the terminal: deletes
  container's rootfs only; worktree mount stays.
- Agent typing the same thing: same.
- Either trying `cat /etc/passwd`: gets the container's, not the
  host's.
- Either trying `cd ..` from the worktree: hits `/`, sees ubuntu
  rootfs only.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from dataclasses import dataclass, field

import structlog

from gapt_server.domains.terminal.pty import PtyHandle, PtySpawnError, spawn_pty

logger = structlog.get_logger(__name__)


DEFAULT_IMAGE = os.environ.get("GAPT_WORKSPACE_SANDBOX_IMAGE", "gapt-workspace:latest")


class WorkspaceSandboxError(RuntimeError):
    """Stable code suffix surfaces to the router layer as HTTP."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkspaceSandboxUnavailable(WorkspaceSandboxError):
    """Raised when Docker isn't installed / reachable. The router
    typically returns 412 with a "install docker" hint."""

    def __init__(self, reason: str) -> None:
        super().__init__("workspace_sandbox.docker_unavailable", reason)


def _container_name(workspace_id: str) -> str:
    """`gapt-ws-<wid>` — short, lowercase, docker-name-safe."""
    return f"gapt-ws-{workspace_id.lower()}"


async def _run_docker(*args: str, timeout_s: float = 30.0) -> tuple[int, str, str]:
    """Run a `docker` subcommand, returning (rc, stdout, stderr).
    Raises `WorkspaceSandboxUnavailable` when the docker binary isn't
    on PATH."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise WorkspaceSandboxUnavailable(
            "`docker` binary not on PATH — install Docker to use the workspace sandbox"
        ) from exc
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise WorkspaceSandboxError(
            "workspace_sandbox.docker_timeout",
            f"docker {' '.join(args[:2])} timed out after {timeout_s}s",
        ) from exc
    return (
        proc.returncode if proc.returncode is not None else -1,
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
    )


@dataclass
class WorkspaceSandbox:
    """Handle to one workspace's container."""

    workspace_id: str
    worktree_path: str
    image: str
    container_name: str

    async def ensure(self) -> None:
        """Idempotent: start the container if it doesn't already
        exist + run. Re-creates it if a previous instance died
        (Docker keeps stopped containers around by name otherwise)."""
        rc, out, _ = await _run_docker(
            "inspect", "-f", "{{.State.Running}}", self.container_name, timeout_s=5.0
        )
        if rc == 0:
            running = out.strip().lower() == "true"
            if running:
                return
            # Stopped container with the same name — remove and start fresh.
            await _run_docker("rm", "-f", self.container_name, timeout_s=10.0)

        # `--init` reaps zombies (`tail -f` style entrypoint can leave
        # them otherwise when the user spawns + kills children).
        # `--label gapt.workspace_id=<wid>` lets `docker ps --filter
        # label=...` find every workspace container for ops.
        rc, _, err = await _run_docker(
            "run",
            "-d",
            "--init",
            "--name",
            self.container_name,
            "--label",
            f"gapt.workspace_id={self.workspace_id}",
            "-v",
            f"{self.worktree_path}:/workspace",
            "-w",
            "/workspace",
            self.image,
            # `tail -f /dev/null` is the cheapest keep-alive — exits
            # only when we kill the container.
            "sh",
            "-c",
            "tail -f /dev/null",
            timeout_s=60.0,
        )
        if rc != 0:
            raise WorkspaceSandboxError(
                "workspace_sandbox.start_failed",
                f"docker run failed: {err.strip()[:400]}",
            )
        logger.info(
            "workspace_sandbox.started",
            workspace_id=self.workspace_id,
            image=self.image,
            container=self.container_name,
        )

    async def exec(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str = "/workspace",
        timeout_s: float = 60.0,
    ) -> tuple[int, bytes, bytes]:
        """`docker exec` an argv list. Returns final (rc, stdout, stderr).

        Don't use for long-running commands — there's a timeout. Use
        `spawn_pty()` for interactive shells, or
        `ServiceRegistry.start()` for dev servers."""
        await self.ensure()
        exec_args = ["exec", "-w", cwd]
        if env:
            for k, v in env.items():
                exec_args += ["--env", f"{k}={v}"]
        exec_args.append(self.container_name)
        exec_args.extend(argv)
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                *exec_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise WorkspaceSandboxUnavailable(str(exc)) from exc
        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            return (-1, b"", f"command timed out after {timeout_s}s".encode())
        return (
            proc.returncode if proc.returncode is not None else -1,
            out_b,
            err_b,
        )

    async def spawn_pty(
        self,
        *,
        cmd: list[str] | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> PtyHandle:
        """Open an interactive PTY into the container via `docker exec
        -it`. Defaults to login bash so .bashrc / nvm shims load.

        Returns a `PtyHandle` the WebSocket router can drive — same
        interface as the native `spawn_pty()`; the only diff is the
        argv is `docker exec -it <container> bash -l` rather than
        plain `bash -l`."""
        await self.ensure()
        shell_cmd = cmd or ["/bin/bash", "-l"]
        argv = [
            "docker",
            "exec",
            "-i",
            "-t",
            "-w",
            "/workspace",
            "--env",
            f"COLUMNS={cols}",
            "--env",
            f"LINES={rows}",
            "--env",
            "TERM=xterm-256color",
            self.container_name,
            *shell_cmd,
        ]
        try:
            return await spawn_pty(cmd=argv, rows=rows, cols=cols)
        except PtySpawnError as exc:
            raise WorkspaceSandboxError(
                "workspace_sandbox.pty_spawn_failed", str(exc)
            ) from exc

    async def spawn_background(
        self,
        *,
        cmd: str,
        log_path_inside: str,
        env: dict[str, str] | None = None,
    ) -> int:
        """Spawn `cmd` as a background process inside the container,
        redirecting stdout+stderr to `log_path_inside` (worktree-
        relative — `/workspace/.gapt/services/<label>.log` lands on
        the host through the bind mount, so `file-tail` on the host
        side just works).

        Returns the *host-side* PID of the `docker exec` process —
        not the container-side PID. Stopping the host-side process
        ends the in-container child too (docker exec propagates
        SIGTERM)."""
        await self.ensure()
        # Build the inner shell command: redirect + exec so the child
        # process replaces the shell (no double-fork zombie).
        inner = f"mkdir -p $(dirname {shlex.quote(log_path_inside)}) && exec {cmd} >> {shlex.quote(log_path_inside)} 2>&1"
        exec_args = ["docker", "exec", "-d", "-w", "/workspace"]
        if env:
            for k, v in env.items():
                exec_args += ["--env", f"{k}={v}"]
        exec_args += [self.container_name, "sh", "-c", inner]
        proc = await asyncio.create_subprocess_exec(
            *exec_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err_b = await proc.communicate()
        if proc.returncode != 0:
            raise WorkspaceSandboxError(
                "workspace_sandbox.spawn_failed",
                f"docker exec -d failed: {err_b.decode('utf-8', errors='replace').strip()[:200]}",
            )
        # docker exec -d returns immediately; no PID. Caller treats
        # the process as managed-via-container, not by PID. Return -1
        # to signal "we don't have one".
        return -1

    async def kill_inside(self, pattern: str) -> None:
        """Kill processes inside the container whose argv matches
        `pattern` (substring grep). Used by ServiceRegistry.stop()
        when running through the sandbox."""
        await self.exec(
            ["sh", "-c", f"pkill -TERM -f {shlex.quote(pattern)} || true"],
            timeout_s=5.0,
        )

    async def stop(self) -> None:
        """Remove the container. Idempotent; no-op if it never started."""
        await _run_docker("rm", "-f", self.container_name, timeout_s=10.0)
        logger.info(
            "workspace_sandbox.stopped",
            workspace_id=self.workspace_id,
            container=self.container_name,
        )


@dataclass
class WorkspaceSandboxManager:
    """Process-scoped registry of WorkspaceSandbox handles. Lazy:
    a sandbox is only created when something calls `.get()`. Avoid
    calling on every request — cache the result in the dependency
    layer if you need it per-request."""

    image: str = DEFAULT_IMAGE
    _entries: dict[str, WorkspaceSandbox] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def get(self, workspace_id: str, worktree_path: str) -> WorkspaceSandbox:
        """Return the handle for `workspace_id`, creating one if
        absent. *Doesn't* `ensure` the container — call
        `sandbox.ensure()` or any method that uses it; ensure is
        idempotent and cheap."""
        existing = self._entries.get(workspace_id)
        if existing is not None and existing.worktree_path == worktree_path:
            return existing
        sandbox = WorkspaceSandbox(
            workspace_id=workspace_id,
            worktree_path=worktree_path,
            image=self.image,
            container_name=_container_name(workspace_id),
        )
        self._entries[workspace_id] = sandbox
        return sandbox

    async def stop(self, workspace_id: str) -> None:
        sandbox = self._entries.pop(workspace_id, None)
        if sandbox is None:
            # Container may still exist from a previous server life —
            # the name is deterministic so try a best-effort remove
            # by name.
            await _run_docker("rm", "-f", _container_name(workspace_id), timeout_s=10.0)
            return
        await sandbox.stop()

    async def aclose(self) -> None:
        """Server shutdown — drop every container we created. Best-
        effort; failures are swallowed (the operator can clean up
        manually via `docker rm`)."""
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for sandbox in entries:
            try:
                await sandbox.stop()
            except Exception:  # noqa: BLE001
                pass
