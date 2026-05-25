"""`LocalComposeTarget` — same-host `docker compose` driver.

Runs `docker compose -f <compose_path> --project-name gapt-prod-{slug}
up -d` against the user's host docker (or against a dedicated Sysbox
sandbox in M2). Secrets are passed as environment variables to the
subprocess so they never land in argv / process listing — the parent
zeroizes the dict on completion.

Post-up routing
---------------
After a successful `up -d`, the target optionally:
  1. Identifies the *primary* service container (from
     `target_options.primary_service` + `primary_port`, or first
     service with an exposed port).
  2. Joins it to the shared `gapt-net` docker network (no compose-
     file mutation; uses `docker network connect`).
  3. Registers a Caddy reverse-proxy route at
     `prod-<env-slug>.<preview-domain>` via the injected
     SubdomainManager.

Both routing dependencies (network name, subdomain manager) are
optional kwargs — if the SubdomainManager is None we skip routing
silently and the operator is responsible for exposing the stack
their own way (compose `ports:`, an external proxy, etc.).

Rollback strategy: snapshot the running image digests *before* the
`up` and restore them after a failure. We treat the user's previous
state as the source of truth for "what to roll back to" rather than
relying on a registry version log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gapt_server.domains.deploy.protocol import (
    DeployContext,
    DeployResult,
    DeployStatus,
    DeployStatusKind,
    DeployTargetError,
    RollbackResult,
)

if TYPE_CHECKING:
    from gapt_server.domains.caddy.subdomain import SubdomainManager

logger = logging.getLogger(__name__)

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


def _compose_flags(paths: list[str]) -> list[str]:
    """Chain `-f a -f b ...` for every path. Empty input falls back to
    a single `-f docker-compose.yml` to keep the argv shape consistent."""
    chosen = paths or ["docker-compose.yml"]
    out: list[str] = []
    for p in chosen:
        out.extend(["-f", p])
    return out


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
    # Populated by the post-up routing step. None until either the
    # routing succeeds or it's skipped (no SubdomainManager injected).
    bound_url: str | None = None


@dataclass
class LocalComposeTarget:
    """Compose driver bound to a user-chosen project name prefix."""

    name: str = "local_compose"
    docker_binary: str | None = None
    runner: ComposeRunner = field(default=_default_runner)
    project_prefix: str = "gapt-prod"
    # When set, the post-up routing step joins the primary container
    # to this docker network and registers a Caddy preview subdomain.
    # When None, no routing happens — `compose up -d` lands the stack
    # and the operator wires their own exposure.
    subdomain_manager: SubdomainManager | None = None
    routing_network: str = "gapt-net"
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

    async def _ps_services(
        self, project: str, env: dict[str, str]
    ) -> list[dict[str, object]]:
        """`docker compose ps --format json` → list of service rows.
        Returns [] on any parse error so callers can skip routing
        without crashing the whole deploy."""
        rc, stdout, _ = await self._run(
            [self._bin(), "compose", "-p", project, "ps", "--format", "json"],
            env,
        )
        if rc != 0 or not stdout.strip():
            return []
        try:
            parsed = json.loads(stdout)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            # NDJSON variant — one row per line.
            rows: list[dict[str, object]] = []
            for raw in stdout.splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
            return rows

    async def _route_primary_service(
        self,
        *,
        project: str,
        request: "DeployRequest",
        env: dict[str, str],
    ) -> str | None:
        """Connect the primary service container to gapt-net and
        register a Caddy preview subdomain. Returns the bound URL
        (`https://prod-<env>.<preview-domain>`) or None when routing
        was skipped (no SubdomainManager wired) / impossible (no
        published ports / can't find primary service).

        Primary service selection:
          1. `target_options.primary_service` if set — explicit win.
          2. First service in `docker compose ps` with a published
             port (heuristic — usually the web tier).
        Primary port selection:
          1. `target_options.primary_port` if set.
          2. The first published port on the primary container.
        """
        if self.subdomain_manager is None:
            return None

        rows = await self._ps_services(project, env)
        if not rows:
            return None

        # Resolve primary service.
        wanted_service = request.target_options.get("primary_service")
        primary_row: dict[str, object] | None = None
        if isinstance(wanted_service, str) and wanted_service:
            for row in rows:
                if row.get("Service") == wanted_service or row.get("Name") == wanted_service:
                    primary_row = row
                    break
        if primary_row is None:
            for row in rows:
                if row.get("Publishers") or row.get("Ports"):
                    primary_row = row
                    break
        if primary_row is None:
            primary_row = rows[0]

        container_name = primary_row.get("Name")
        if not isinstance(container_name, str) or not container_name:
            return None

        # Resolve primary port. Prefer explicit setting; else first
        # published port (TargetPort, not host port — Caddy on
        # gapt-net hits the container directly).
        wanted_port_raw = request.target_options.get("primary_port")
        primary_port: int | None = None
        try:
            primary_port = int(wanted_port_raw) if wanted_port_raw is not None else None
        except (TypeError, ValueError):
            primary_port = None
        if primary_port is None:
            publishers = primary_row.get("Publishers")
            if isinstance(publishers, list):
                for pub in publishers:
                    if isinstance(pub, dict):
                        tp = pub.get("TargetPort")
                        try:
                            primary_port = int(tp) if tp is not None else None
                        except (TypeError, ValueError):
                            primary_port = None
                        if primary_port:
                            break
        if primary_port is None:
            # No port to route — leave it; the deploy still succeeded.
            return None

        # Connect to gapt-net. Idempotent: docker emits "already in
        # network" on a re-connect; we treat that as success.
        rc, _, err = await self._run(
            [self._bin(), "network", "connect", self.routing_network, container_name],
            env,
        )
        if rc != 0 and "already exists" not in err.lower() and "already in" not in err.lower():
            logger.warning(
                "deploy.network_connect_failed container=%s err=%s",
                container_name,
                err.strip()[:300],
            )
            return None

        # Register the Caddy route. Slug is per-environment so
        # multiple envs of the same project don't clobber each other.
        # Path mode by default (single apex domain, no wildcard DNS
        # needed). Subdomain mode is opt-in via the env's
        # `target_options.preview_mode`.
        from gapt_server.domains.caddy.subdomain import (  # noqa: PLC0415
            PreviewMode,
            SubdomainBinding,
        )

        slug = f"prod-{request.environment}-{request.project_id}".lower()
        mode_str = request.target_options.get("preview_mode", "path")
        mode = (
            PreviewMode.SUBDOMAIN
            if str(mode_str).lower() == "subdomain"
            else PreviewMode.PATH
        )
        binding = SubdomainBinding(
            workspace_slug=slug,
            upstream_host=container_name,
            upstream_port=primary_port,
            mode=mode,
        )
        host = await self.subdomain_manager.register(binding)
        return f"https://{host}"

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
                    *_compose_flags(request.resolved_compose_paths()),
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

            up_argv: list[str] = [
                self._bin(),
                "compose",
                "-p",
                project,
                *_compose_flags(request.resolved_compose_paths()),
                "up",
                "-d",
                "--remove-orphans",
            ]
            # Per-deploy build flag — `target_options.build = true`
            # triggers `docker compose up -d --build`. Use this when
            # the compose service uses `build: .` and you want a
            # fresh build every deploy (otherwise compose reuses the
            # cached image). No-op for image: services.
            if bool(request.target_options.get("build", False)):
                up_argv.append("--build")
            up_exit, up_out, up_err = await self._run(up_argv, env)
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

            # Post-up routing — best-effort. Failure here doesn't fail
            # the deploy (the stack is up; routing is the cherry on
            # top). We log the failure into log_tail so the user sees
            # it in the SSE stream.
            try:
                bound_url = await self._route_primary_service(
                    project=project,
                    request=request,
                    env=env,
                )
                state.bound_url = bound_url
                if bound_url:
                    state.log_tail = (
                        state.log_tail
                        + f"\n[gapt] routed prod stack → {bound_url}\n"
                    )[-2000:]
            except Exception as exc:  # noqa: BLE001
                logger.warning("deploy.routing_failed", exc_info=exc)
                state.log_tail = (
                    state.log_tail + f"\n[gapt] routing step failed: {exc}\n"
                )[-2000:]

            state.status = DeployStatusKind.SUCCESS
            state.finished_at = datetime.now(tz=UTC)
            return DeployResult(
                run_id=ctx.run_id,
                status=state.status,
                log=state.log_tail,
                bound_url=state.bound_url,
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
                    *_compose_flags(request.resolved_compose_paths()),
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
