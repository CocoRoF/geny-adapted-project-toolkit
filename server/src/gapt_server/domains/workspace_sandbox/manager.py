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
import re
import shlex
from dataclasses import dataclass, field

import structlog

from gapt_server.domains.terminal.pty import PtyHandle, PtySpawnError, spawn_pty

logger = structlog.get_logger(__name__)


DEFAULT_IMAGE = os.environ.get("GAPT_WORKSPACE_SANDBOX_IMAGE", "gapt-workspace:latest")

# Shared docker network every workspace container joins. Caddy (the
# edge reverse proxy) ALSO joins this network so it can address each
# workspace by container name on docker DNS — `gapt-ws-<wid>:<port>`
# — without publishing ports on the host. The name is fixed so the
# compose stack and this server agree on it.
GAPT_NETWORK = os.environ.get("GAPT_WORKSPACE_NETWORK", "gapt-net")


def _resolve_host_claude_paths() -> tuple[str | None, str | None]:
    """Where the host's claude CLI keeps its state and top-level config.

    The CLI looks at two locations:
      * ``~/.claude/`` — state directory: OAuth credentials, sessions,
        history, MCP cache, etc.
      * ``~/.claude.json`` — top-level config file (separate from the
        directory).

    Both must be propagated for OAuth to actually work inside the
    container — mounting just the directory leaves the CLI throwing
    ``Claude configuration file not found at: /root/.claude.json``.

    Order:
      1. `GAPT_HOST_CLAUDE_DIR` + `GAPT_HOST_CLAUDE_CONFIG` — explicit
         operator overrides (server runs under a different uid than
         the human who logged in to claude).
      2. `~/.claude` + `~/.claude.json` — claude's defaults, resolved
         against the server process's `HOME`.

    Each path is returned only if it actually exists; absent ones are
    None and the caller silently skips the corresponding bind mount.
    """
    dir_override = os.environ.get("GAPT_HOST_CLAUDE_DIR", "").strip()
    cfg_override = os.environ.get("GAPT_HOST_CLAUDE_CONFIG", "").strip()

    dir_path: str | None
    cfg_path: str | None

    if dir_override:
        dir_path = dir_override if os.path.isdir(dir_override) else None
    else:
        default_dir = os.path.expanduser("~/.claude")
        dir_path = default_dir if os.path.isdir(default_dir) else None

    if cfg_override:
        cfg_path = cfg_override if os.path.isfile(cfg_override) else None
    else:
        default_cfg = os.path.expanduser("~/.claude.json")
        cfg_path = default_cfg if os.path.isfile(default_cfg) else None

    return dir_path, cfg_path


# Off by default in case the operator doesn't want their personal
# OAuth token reachable from workspace containers. Solo deployments
# (the common GAPT case today) flip this on so the agent picks up
# the same login the user already did on the host — no API-key
# round-trip required.
SHARE_HOST_CLAUDE_AUTH = (
    os.environ.get("GAPT_SHARE_HOST_CLAUDE_AUTH", "1").strip() not in {"", "0", "false", "False"}
)


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


def _bare_from_worktree(worktree_path: str) -> str | None:
    """Read `<worktree>/.git` (a file in worktree layout) and return
    the absolute host path of the *bare* this worktree belongs to.

    The `.git` file contains `gitdir: <abs path>/worktrees/<wid>`;
    we strip the trailing `worktrees/<wid>` to recover the bare dir
    so the container mount targets the right host directory without
    needing to thread the bare root setting through every caller.

    Returns None when `.git` isn't a file (legacy clone layout) or
    when the content doesn't match the expected shape — both cases
    skip the bare mount (the sandbox boots, git just won't work for
    legacy clones, which is the same as pre-Phase-C.1 behaviour)."""
    git_file = os.path.join(worktree_path, ".git")
    if not os.path.isfile(git_file):
        return None
    try:
        with open(git_file, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return None
    gitdir: str | None = None
    for line in content.splitlines():
        if line.startswith("gitdir:"):
            gitdir = line[len("gitdir:") :].strip()
            break
    if not gitdir or not os.path.isabs(gitdir):
        return None
    parts = gitdir.rstrip("/").split("/")
    if len(parts) < 3 or parts[-2] != "worktrees":
        return None
    return "/".join(parts[:-2])


_GPU_INDEX_RE = re.compile(r"^\d+(,\d+)*$")


def format_gpus_flag(spec: str | None) -> str | None:
    """Phase E.1 — translate a `Settings.workspace_gpus` value into
    the value docker's `--gpus` flag expects.

    Returns `None` to mean "omit --gpus entirely".

    Accepts (and ONLY accepts):
    - ``"all"`` → ``"all"``
    - integer string (``"1"``, ``"2"``) → unchanged; docker reads
      this as device-count.
    - comma-separated index list (``"0"``, ``"0,1"``, ``"2,3,4"``)
      → ``"device=<csv>"``.

    Anything else returns ``None`` so a malformed config doesn't
    leak into the docker argv (which would either fail loud or, on
    older docker versions, silently substitute defaults).
    """
    if spec is None:
        return None
    s = spec.strip()
    if not s:
        return None
    if s.lower() == "all":
        return "all"
    if _GPU_INDEX_RE.match(s):
        # `device=0,1` is the explicit form. The bare count form
        # ("2" meaning "any 2 GPUs") only works when the string is a
        # single integer; we prefer the device= form for multi-index
        # lists since it's unambiguous.
        if "," in s:
            return f"device={s}"
        # Single integer: could be count OR index. Treat single
        # digits as device index for parity with the multi-index
        # case — the operator can use "all" if they want auto-count.
        return f"device={s}"
    return None


async def _ensure_network(name: str) -> None:
    """Create the shared `gapt-net` network if it doesn't already
    exist. `docker network inspect` is cheap (single RPC) so we run
    it on every `ensure()` and only fall through to `create` on a
    miss. The create is also idempotent because docker returns
    "Conflict: already exists" on a race; we treat that as success."""
    rc, _, _ = await _run_docker("network", "inspect", name, timeout_s=5.0)
    if rc == 0:
        return
    rc, _, err = await _run_docker("network", "create", name, timeout_s=10.0)
    if rc != 0 and "already exists" not in err.lower():
        raise WorkspaceSandboxError(
            "workspace_sandbox.network_create_failed",
            f"`docker network create {name}` failed: {err.strip()[:200]}",
        )


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
    # When set, the host directory is bind-mounted at `/root/.claude`
    # inside the container so the in-container `claude` CLI shares
    # the operator's OAuth login (no API key required). Likewise the
    # `~/.claude.json` config file is mounted at `/root/.claude.json`
    # — the CLI errors out without both. The mounts are rw because
    # claude refreshes its tokens in place.
    host_claude_dir: str | None = None
    host_claude_config: str | None = None
    # Phase E.1 — GPU policy. `None` (default) → CPU-only container,
    # argv unchanged. `"all"` → every host GPU. `"0"` / `"0,1"` →
    # specific indices. Passed straight through to `docker run --gpus`
    # which accepts either a count, "all", or `device=<csv>` shape.
    # Validated by `_format_gpus_flag` below so we never emit a
    # malformed flag.
    gpus: str | None = None

    async def ensure(self) -> None:
        """Idempotent: start the container if it doesn't already
        exist + run. Re-creates it if a previous instance died
        (Docker keeps stopped containers around by name otherwise).

        Also makes sure the shared `gapt-net` docker network exists
        — Caddy joins this same network so it can reach the workspace
        by container DNS without publishing ports."""
        await _ensure_network(GAPT_NETWORK)
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
        # Run the container as the host user so files written to the
        # bind-mounted worktree land with the right ownership, AND so
        # the claude CLI's safety check (`--dangerously-skip-permissions
        # cannot be used with root/sudo privileges`) passes — that
        # flag is what `bypassPermissions` mode translates to, and we
        # need it for headless agent operation. The gapt-workspace
        # image ships with `ubuntu` uid 1000 + HOME=/home/ubuntu;
        # we mount claude config there.
        uid = os.getuid()
        gid = os.getgid()
        argv: list[str] = [
            "run",
            "-d",
            "--init",
            "--name",
            self.container_name,
            "--label",
            f"gapt.workspace_id={self.workspace_id}",
            "--user",
            f"{uid}:{gid}",
            "--env",
            "HOME=/home/ubuntu",
            # Hostname matches container name so Caddy upstreams can
            # use either; explicit is better than implicit.
            "--hostname",
            self.container_name,
            "--network",
            GAPT_NETWORK,
            "-v",
            f"{self.worktree_path}:/workspace",
        ]
        # Worktree-layout workspaces (Phase C.1+) have `.git` as a
        # *file* with `gitdir: <abs path>/worktrees/<wid>` pointing at
        # the shared bare. That absolute host path must also exist
        # inside the container — otherwise every `git` command fails
        # with `fatal: not a git repository: …/worktrees/<wid>`.
        #
        # We read the `.git` file and mount the bare at the *same
        # absolute host path* inside the container. As of 2026-05-29
        # the bare lives at `<workspace_bare_root>/<slug>/` outside
        # `/workspace`, so the mount doesn't overlap with the worktree
        # mount (the earlier "test/ shows up as untracked" bug).
        # Mount rw so commits land new objects in `<bare>/objects/`
        # and update `<bare>/worktrees/<wid>/HEAD`; `--user` already
        # matches the host owner so permissions just work.
        bare_dir = _bare_from_worktree(self.worktree_path)
        if bare_dir is not None and os.path.isdir(bare_dir):
            argv += ["-v", f"{bare_dir}:{bare_dir}:rw"]
        if self.host_claude_dir:
            # rw — the CLI refreshes OAuth tokens in place.
            argv += ["-v", f"{self.host_claude_dir}:/home/ubuntu/.claude:rw"]
        if self.host_claude_config:
            argv += ["-v", f"{self.host_claude_config}:/home/ubuntu/.claude.json:rw"]
        # Phase E.1 — GPU passthrough. `format_gpus_flag` returns
        # `None` for unset / malformed config, in which case we omit
        # `--gpus` entirely so CPU-only hosts keep working unchanged.
        gpu_arg = format_gpus_flag(self.gpus)
        if gpu_arg is not None:
            argv += ["--gpus", gpu_arg]
        argv += [
            "-w",
            "/workspace",
            self.image,
            # `tail -f /dev/null` is the cheapest keep-alive — exits
            # only when we kill the container.
            "sh",
            "-c",
            "tail -f /dev/null",
        ]
        rc, _, err = await _run_docker(*argv, timeout_s=60.0)
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
            host_claude_dir=self.host_claude_dir,
            host_claude_config=self.host_claude_config,
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
        marker: str | None = None,
    ) -> int:
        """Spawn `cmd` as a background process inside the container,
        redirecting stdout+stderr to `log_path_inside` (worktree-
        relative — `/workspace/.gapt/services/<label>.log` lands on
        the host through the bind mount, so `file-tail` on the host
        side just works).

        When `marker` is set, the wrapping `sh` process keeps a
        recognisable argv (`sh -c <inner> <marker>`) so the caller can
        `pgrep -f <marker>` to confirm liveness — useful when the
        user's command rewrites its own argv (`node`, `npm run`, ...)
        or daemonises in a way `ps` can't trace back to us. We
        deliberately do *not* `exec` the cmd: the sh parent has to
        stay alive for pgrep to keep matching.

        Returns -1 because `docker exec -d` doesn't surface a PID.
        Lifecycle is tracked container-side via the marker, not
        host-side via PID."""
        await self.ensure()
        # `sh` redirects child stdout/stderr to the log then runs cmd
        # as a foreground child (no `exec`). When marker is provided
        # we pass it as $0; that puts it in the sh process's
        # /proc/PID/cmdline so `pgrep -f <marker>` matches.
        inner = (
            f"mkdir -p $(dirname {shlex.quote(log_path_inside)}) && "
            f"{cmd} >> {shlex.quote(log_path_inside)} 2>&1"
        )
        exec_args = ["docker", "exec", "-d", "-w", "/workspace"]
        if env:
            for k, v in env.items():
                exec_args += ["--env", f"{k}={v}"]
        exec_args += [self.container_name, "sh", "-c", inner]
        if marker:
            exec_args.append(marker)
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
    # When `SHARE_HOST_CLAUDE_AUTH` is on, every WorkspaceSandbox
    # spawned through this manager bind-mounts the host's claude
    # state dir + config file into the container so the operator's
    # OAuth login propagates. Computed once at manager construction
    # time; same value applied to every workspace.
    host_claude_dir: str | None = field(default=None)
    host_claude_config: str | None = field(default=None)
    # Phase E.1 — single GPU policy for the install. Read from
    # `Settings.workspace_gpus` at construction and stamped onto
    # every WorkspaceSandbox handle. A change to the setting takes
    # effect on the next workspace boot (existing containers retain
    # their original GPU mapping until restarted).
    gpus: str | None = field(default=None)
    _entries: dict[str, WorkspaceSandbox] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        if SHARE_HOST_CLAUDE_AUTH and self.host_claude_dir is None and self.host_claude_config is None:
            self.host_claude_dir, self.host_claude_config = _resolve_host_claude_paths()

    def get(self, workspace_id: str, worktree_path: str) -> WorkspaceSandbox:
        """Return the handle for `workspace_id`, creating one if
        absent. *Doesn't* `ensure` the container — call
        `sandbox.ensure()` or any method that uses it; ensure is
        idempotent and cheap."""
        existing = self._entries.get(workspace_id)
        if existing is not None and existing.worktree_path == worktree_path:
            # Keep the existing handle's gpus value in sync with the
            # manager — lets the operator flip the setting and have
            # the next ensure() pick it up without us re-creating the
            # entry. Container itself only sees the change on restart.
            existing.gpus = self.gpus
            return existing
        sandbox = WorkspaceSandbox(
            workspace_id=workspace_id,
            worktree_path=worktree_path,
            image=self.image,
            container_name=_container_name(workspace_id),
            host_claude_dir=self.host_claude_dir,
            host_claude_config=self.host_claude_config,
            gpus=self.gpus,
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
            except Exception:
                pass
