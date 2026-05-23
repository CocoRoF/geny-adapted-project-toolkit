"""`RemoteSshTarget` — same compose verbs against a remote host over SSH.

The private key is supplied per-deploy (read from Secret Vault by the
orchestrator) and never written to disk. We load it directly into
asyncssh — that holds it in process memory only.

Secrets travel via `SendEnv` in the SSH config; `AcceptEnv` on the
remote side decides which ones reach the shell. For convenience we
also prepend them to the remote command as `KEY=val` to survive
hosts that don't allow `SendEnv`. After the run we zeroize the
plaintext dict.
"""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from gapt_server.domains.deploy.protocol import (
    DeployContext,
    DeployResult,
    DeployStatus,
    DeployStatusKind,
    DeployTargetError,
    RollbackResult,
)

# asyncssh is imported lazily inside `_default_runner` so the test
# runner can patch it without paying its full load cost.


SshRunner = Callable[
    ["SshConnectionSpec", str, dict[str, str]],
    Awaitable[tuple[int, str, str]],
]


@dataclass(frozen=True)
class SshConnectionSpec:
    """What the orchestrator hands the runner per deploy."""

    host: str
    user: str
    port: int = 22
    private_key_pem: str = ""  # PEM-encoded, lives in memory only
    known_hosts: str | None = None  # explicit allow-list; None = strict


async def _default_runner(
    spec: SshConnectionSpec,
    command: str,
    env: dict[str, str],
) -> tuple[int, str, str]:
    # asyncssh is the only async-native option that supports
    # in-memory key loading cleanly. Lazy import so the test runner
    # can patch it without paying the load cost.
    import asyncssh  # noqa: PLC0415

    if not spec.private_key_pem:
        raise DeployTargetError(
            "deploy.ssh.no_key",
            "RemoteSshTarget requires a private_key_pem (read from Secret Vault)",
        )

    try:
        key = asyncssh.import_private_key(spec.private_key_pem)
    except (asyncssh.KeyImportError, ValueError) as exc:
        raise DeployTargetError(
            "deploy.ssh.bad_key",
            f"private_key_pem rejected by asyncssh: {exc}",
        ) from exc

    connect_kwargs: dict[str, object] = {
        "host": spec.host,
        "username": spec.user,
        "port": spec.port,
        "client_keys": [key],
    }
    if spec.known_hosts is not None:
        connect_kwargs["known_hosts"] = spec.known_hosts

    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            # Prepend env so the remote shell sees them — `SendEnv`
            # requires server-side `AcceptEnv` whitelisting which the
            # user can't always control.
            prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
            full = f"{prefix} {command}" if prefix else command
            result = await conn.run(full, check=False)
            return (
                int(result.returncode or 0),
                str(result.stdout or ""),
                str(result.stderr or ""),
            )
    except (OSError, asyncssh.Error) as exc:
        raise DeployTargetError(
            "deploy.ssh.transport",
            f"ssh {spec.user}@{spec.host}:{spec.port} failed: {exc}",
        ) from exc


@dataclass
class _RunState:
    status: DeployStatusKind = DeployStatusKind.PENDING
    log_tail: str = ""
    exec_code: str | None = None
    finished_at: datetime | None = None


@dataclass
class RemoteSshTarget:
    """SSH-backed compose driver. The orchestrator builds an
    `SshConnectionSpec` per deploy from project settings + Secret
    Vault and stashes it on `request.target_options['ssh']`."""

    name: str = "remote_ssh"
    runner: SshRunner = field(default=_default_runner)
    project_prefix: str = "gapt-prod"
    _runs: dict[str, _RunState] = field(default_factory=dict)

    def _project(self, project_id: str) -> str:
        return f"{self.project_prefix}-{project_id.lower()}"

    def _spec(self, ctx: DeployContext) -> SshConnectionSpec:
        opts = ctx.request.target_options.get("ssh")
        if not isinstance(opts, dict):
            raise DeployTargetError(
                "deploy.ssh.spec_missing",
                "request.target_options['ssh'] must be a mapping",
            )
        try:
            return SshConnectionSpec(
                host=str(opts["host"]),
                user=str(opts["user"]),
                port=int(opts.get("port", 22)),
                private_key_pem=str(opts.get("private_key_pem", "")),
                known_hosts=(
                    str(opts["known_hosts"]) if opts.get("known_hosts") is not None else None
                ),
            )
        except KeyError as exc:
            raise DeployTargetError(
                "deploy.ssh.spec_missing",
                f"missing ssh option: {exc}",
            ) from exc

    async def deploy(self, ctx: DeployContext) -> DeployResult:
        request = ctx.request
        spec = self._spec(ctx)
        project = self._project(request.project_id)
        env = dict(request.env_secrets)
        state = _RunState(status=DeployStatusKind.RUNNING)
        self._runs[ctx.run_id] = state

        try:
            pull_cmd = (
                f"docker compose -p {shlex.quote(project)} "
                f"-f {shlex.quote(request.compose_path)} pull"
            )
            up_cmd = (
                f"docker compose -p {shlex.quote(project)} "
                f"-f {shlex.quote(request.compose_path)} up -d --remove-orphans"
            )

            try:
                pull_exit, pull_out, pull_err = await self.runner(spec, pull_cmd, env)
            except DeployTargetError as exc:
                state.status = DeployStatusKind.FAILED
                state.exec_code = exc.code
                state.log_tail = str(exc)[:2000]
                state.finished_at = datetime.now(tz=UTC)
                return DeployResult(
                    run_id=ctx.run_id,
                    status=state.status,
                    log=state.log_tail,
                    exec_code=state.exec_code,
                )

            state.log_tail = (pull_out + pull_err)[-2000:]
            if pull_exit != 0:
                state.status = DeployStatusKind.FAILED
                state.exec_code = "deploy.compose_pull_failed"
                state.finished_at = datetime.now(tz=UTC)
                return DeployResult(
                    run_id=ctx.run_id,
                    status=state.status,
                    log=state.log_tail,
                    exec_code=state.exec_code,
                )

            up_exit, up_out, up_err = await self.runner(spec, up_cmd, env)
            state.log_tail = (state.log_tail + up_out + up_err)[-2000:]
            if up_exit != 0:
                state.status = DeployStatusKind.FAILED
                state.exec_code = "deploy.compose_up_failed"
                state.finished_at = datetime.now(tz=UTC)
                return DeployResult(
                    run_id=ctx.run_id,
                    status=state.status,
                    log=state.log_tail,
                    exec_code=state.exec_code,
                )

            state.status = DeployStatusKind.SUCCESS
            state.finished_at = datetime.now(tz=UTC)
            return DeployResult(
                run_id=ctx.run_id, status=state.status, log=state.log_tail
            )
        finally:
            for key in list(env.keys()):
                env[key] = ""

    async def status(self, ctx: DeployContext) -> DeployStatus:
        state = self._runs.get(ctx.run_id)
        if state is None:
            return DeployStatus(run_id=ctx.run_id, status=DeployStatusKind.PENDING)
        return DeployStatus(
            run_id=ctx.run_id,
            status=state.status,
            log_tail=state.log_tail,
            exec_code=state.exec_code,
            finished_at=state.finished_at,
        )

    async def rollback(self, ctx: DeployContext, *, to_version: str) -> RollbackResult:
        """Compose `up` with the prior image tag interpolated via env.
        The user's compose file must reference `${IMAGE_TAG}` (or a
        similar variable) for this to work — same convention as the
        local target."""
        spec = self._spec(ctx)
        project = self._project(ctx.request.project_id)
        env = {**ctx.request.env_secrets, "GAPT_ROLLBACK_TAG": to_version}
        try:
            cmd = (
                f"docker compose -p {shlex.quote(project)} "
                f"-f {shlex.quote(ctx.request.compose_path)} up -d"
            )
            exit_code, stdout, stderr = await self.runner(spec, cmd, env)
            log = (stdout + stderr)[-2000:]
            if exit_code != 0:
                return RollbackResult(
                    run_id=ctx.run_id,
                    status=DeployStatusKind.FAILED,
                    restored_version=to_version,
                    log=log,
                    exec_code="deploy.rollback_failed",
                )
            return RollbackResult(
                run_id=ctx.run_id,
                status=DeployStatusKind.ROLLED_BACK,
                restored_version=to_version,
                log=log,
            )
        finally:
            for key in list(env.keys()):
                env[key] = ""
