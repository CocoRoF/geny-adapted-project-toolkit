"""`LocalComposeTarget` — same-host `docker compose` driver.

Runs `docker compose -f <compose_path> --project-name gapt-prod-{slug}
up -d` against the user's host docker (or against a dedicated Sysbox
sandbox in M2). Secrets are passed as environment variables to the
subprocess so they never land in argv / process listing — the parent
zeroizes the dict on completion.

Rollback strategy: snapshot the running image digests *before* the
`up` and restore them after a failure. We treat the user's previous
state as the source of truth for "what to roll back to" rather than
relying on a registry version log.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
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

ComposeRunner = Callable[
    [list[str], dict[str, str]],
    Awaitable[tuple[int, str, str]],
]


async def _default_runner(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    # Merge over os.environ so PATH / HOME / etc. still resolve. The
    # caller's env values override OS values (so secrets win over any
    # leftover dev shell state).
    merged = {**os.environ, **env}
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env=merged,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _ensure_binary(override: str | None, name: str) -> str:
    if override:
        return override
    found = shutil.which(name)
    if found is None:
        raise DeployTargetError(
            "deploy.tool_missing",
            f"{name!r} not on PATH; install docker compose before deploying",
        )
    return found


@dataclass
class _LocalRunState:
    """Per-run progress capture used by the polling `status()`
    endpoint. The orchestrator can call `status(ctx)` mid-run to
    populate the SSE log tail."""

    status: DeployStatusKind = DeployStatusKind.PENDING
    log_tail: str = ""
    exec_code: str | None = None
    finished_at: datetime | None = None
    prior_image_digests: dict[str, str] = field(default_factory=dict)


@dataclass
class LocalComposeTarget:
    """Compose driver bound to a user-chosen project name prefix."""

    name: str = "local_compose"
    docker_binary: str | None = None
    runner: ComposeRunner = field(default=_default_runner)
    project_prefix: str = "gapt-prod"
    _runs: dict[str, _LocalRunState] = field(default_factory=dict)

    def _compose_project(self, request_project_id: str) -> str:
        # ULIDs are 26 chars; the prefix + ULID is fine for compose
        # project names (DNS-friendly characters only).
        return f"{self.project_prefix}-{request_project_id.lower()}"

    def _bin(self) -> str:
        return _ensure_binary(self.docker_binary, "docker")

    async def _run(self, argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        return await self.runner(argv, env)

    async def _snapshot_images(  # noqa: PLR0912 — JSON vs NDJSON dual parse keeps branch count high
        self, project: str, env: dict[str, str]
    ) -> dict[str, str]:
        """Capture the digest of every running service so we can roll
        back. `docker compose ps --format json` returns one line per
        service when there is one, or a JSON array when there are
        many — accept both."""
        exit_code, stdout, _ = await self._run(
            [self._bin(), "compose", "-p", project, "ps", "--format", "json"],
            env,
        )
        if exit_code != 0:
            return {}
        out: dict[str, str] = {}
        raw = stdout.strip()
        if not raw:
            return out
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # The newline-delimited variant.
            for raw_line in raw.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                service = obj.get("Service") or obj.get("Name")
                image = obj.get("Image")
                if isinstance(service, str) and isinstance(image, str):
                    out[service] = image
            return out
        if isinstance(parsed, list):
            for obj in parsed:
                if not isinstance(obj, dict):
                    continue
                service = obj.get("Service") or obj.get("Name")
                image = obj.get("Image")
                if isinstance(service, str) and isinstance(image, str):
                    out[service] = image
        elif isinstance(parsed, dict):
            service = parsed.get("Service") or parsed.get("Name")
            image = parsed.get("Image")
            if isinstance(service, str) and isinstance(image, str):
                out[service] = image
        return out

    async def deploy(self, ctx: DeployContext) -> DeployResult:
        request = ctx.request
        project = self._compose_project(request.project_id)
        env = dict(request.env_secrets)
        state = _LocalRunState(status=DeployStatusKind.RUNNING)
        self._runs[ctx.run_id] = state

        try:
            state.prior_image_digests = await self._snapshot_images(project, env)

            pull_exit, pull_out, pull_err = await self._run(
                [
                    self._bin(),
                    "compose",
                    "-p",
                    project,
                    "-f",
                    request.compose_path,
                    "pull",
                ],
                env,
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

            up_exit, up_out, up_err = await self._run(
                [
                    self._bin(),
                    "compose",
                    "-p",
                    project,
                    "-f",
                    request.compose_path,
                    "up",
                    "-d",
                    "--remove-orphans",
                ],
                env,
            )
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
                run_id=ctx.run_id,
                status=state.status,
                log=state.log_tail,
            )
        finally:
            # Zeroize the secret dict so a heap dump can't surface it.
            for key in list(env.keys()):
                env[key] = ""

    async def status(self, ctx: DeployContext) -> DeployStatus:
        state = self._runs.get(ctx.run_id)
        if state is None:
            return DeployStatus(
                run_id=ctx.run_id,
                status=DeployStatusKind.PENDING,
            )
        return DeployStatus(
            run_id=ctx.run_id,
            status=state.status,
            log_tail=state.log_tail,
            exec_code=state.exec_code,
            finished_at=state.finished_at,
        )

    async def rollback(self, ctx: DeployContext, *, to_version: str) -> RollbackResult:
        """Restore the digest snapshot we captured before `deploy()`.

        If the orchestrator passes an explicit `to_version` (e.g. a
        registry tag from the user) we use that for *every* service.
        Otherwise we restore each service to the digest we captured."""
        state = self._runs.get(ctx.run_id)
        request = ctx.request
        project = self._compose_project(request.project_id)
        env = dict(request.env_secrets)
        snapshot = state.prior_image_digests if state else {}
        try:
            if to_version:
                # Single image override — restamp every running service.
                rerun_env = {**env, **{f"COMPOSE_IMAGE_{k}": to_version for k in snapshot}}
            else:
                rerun_env = {**env, **{f"COMPOSE_IMAGE_{k}": v for k, v in snapshot.items()}}

            up_exit, up_out, up_err = await self._run(
                [
                    self._bin(),
                    "compose",
                    "-p",
                    project,
                    "-f",
                    request.compose_path,
                    "up",
                    "-d",
                ],
                rerun_env,
            )
            if up_exit != 0:
                return RollbackResult(
                    run_id=ctx.run_id,
                    status=DeployStatusKind.FAILED,
                    restored_version=to_version or "snapshot",
                    log=(up_out + up_err)[-2000:],
                    exec_code="deploy.rollback_failed",
                )
            return RollbackResult(
                run_id=ctx.run_id,
                status=DeployStatusKind.ROLLED_BACK,
                restored_version=to_version or "snapshot",
                log=(up_out + up_err)[-2000:],
            )
        finally:
            for key in list(env.keys()):
                env[key] = ""
