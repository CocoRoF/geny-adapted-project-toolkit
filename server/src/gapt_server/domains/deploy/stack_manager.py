"""Post-deploy lifecycle ops for prod compose stacks.

The deploy orchestrator only handles the *forward* path — build,
push, `up -d`, route. Once a stack is live, the operator needs to
manage it: tear it down, restart it, look at what's actually
running. That's this module.

We never run `docker compose -f file.yml ...` here — the compose
file path is on the Environment row in the DB and may have moved.
Instead we drive everything by the **compose project label**
(`com.docker.compose.project=gapt-prod-<env_id>`), which docker
stamps on every container at create time. That label is stable
across compose-file relocations and is enough for `stop / restart /
ps` since docker tracks containers by label.

For a fresh `up -d` after a teardown, route the operator back to
the regular Deploy button — that path knows where the compose file
lives and reruns the orchestrator.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import docker

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StackService:
    """One container in the stack — surfaced as a row in the UI's
    Stack section so the operator can see per-service state."""

    container_id: str
    container_name: str
    service: str  # compose service name from the label
    image: str
    status: str  # docker State.Status: running / exited / paused / …
    health: str | None  # State.Health.Status when healthcheck is present
    started_at: str | None
    exit_code: int | None


@dataclass(frozen=True)
class StackStatus:
    project: str
    services: list[StackService]
    running_count: int
    total_count: int


@dataclass(frozen=True)
class StackOpResult:
    """Outcome of a stop/restart op. `output` is whatever docker
    printed (truncated to the last few KiB so the API doesn't ship
    a 10 MiB log)."""

    project: str
    action: str
    ok: bool
    affected: int
    output: str


class StackManager:
    """Thin wrapper around the docker SDK + `docker compose` CLI."""

    def __init__(self, client: "docker.DockerClient") -> None:
        self._client = client

    @staticmethod
    def project_for(project_id: str) -> str:
        """Mirror of `LocalComposeTarget._compose_project`. The
        compose-project name keys on the GAPT *project_id* (the
        owning project), NOT the environment_id — same project's
        envs share a stack name today. Both sides must agree or
        status lookups miss the running containers."""
        return f"gapt-prod-{project_id.lower()}"

    async def status(self, project_id: str) -> StackStatus:
        project = self.project_for(project_id)
        containers = await asyncio.to_thread(
            self._client.containers.list,
            all=True,
            filters={"label": f"com.docker.compose.project={project}"},
        )
        services: list[StackService] = []
        for c in containers:
            attrs = c.attrs
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            state = attrs.get("State") or {}
            health = (state.get("Health") or {}).get("Status") if state.get("Health") else None
            services.append(
                StackService(
                    container_id=attrs.get("Id", ""),
                    container_name=(attrs.get("Name") or "").lstrip("/"),
                    service=labels.get("com.docker.compose.service", ""),
                    image=(attrs.get("Config") or {}).get("Image", ""),
                    status=state.get("Status", "unknown"),
                    health=health,
                    started_at=state.get("StartedAt"),
                    exit_code=state.get("ExitCode") if state.get("Status") == "exited" else None,
                )
            )
        # Stable sort: services alphabetically (matches `compose ps`
        # output the operator's used to).
        services.sort(key=lambda s: (s.service, s.container_name))
        running = sum(1 for s in services if s.status == "running")
        return StackStatus(
            project=project,
            services=services,
            running_count=running,
            total_count=len(services),
        )

    async def stop(self, project_id: str) -> StackOpResult:
        """`docker compose -p <project> down` — stops + removes
        containers and the implicit compose network. Volumes are
        kept (we don't pass `-v`) so the operator's data survives a
        teardown/redeploy cycle."""
        project = self.project_for(project_id)
        rc, output = await self._compose_cli(["-p", project, "down", "--remove-orphans"])
        affected = 0
        # Best-effort affected count — re-list after the op.
        try:
            after = await asyncio.to_thread(
                self._client.containers.list,
                all=True,
                filters={"label": f"com.docker.compose.project={project}"},
            )
            affected = max(0, 0 - len(after))  # negative means "all gone"
        except Exception:  # noqa: BLE001
            pass
        return StackOpResult(
            project=project,
            action="down",
            ok=rc == 0,
            affected=affected,
            output=output[-4096:],
        )

    async def logs(
        self, project_id: str, *, tail: int = 200, since: str | None = None
    ) -> StackOpResult:
        """`docker compose -p <project> logs --tail N [--since S]`.

        Used by the UI to surface live stack output, polled every
        few seconds while the operator watches. `tail` caps the
        response size (200 lines ~ a few KB); `since` filters to
        events after the given timestamp/duration string (e.g.
        `5s`, `2026-05-27T14:00:00Z`).

        Returns the raw merged stdout+stderr from `docker compose
        logs` — caller renders it verbatim. Newest lines are at the
        bottom; that's the compose-CLI convention, not flipped."""
        project = self.project_for(project_id)
        args = ["-p", project, "logs", "--no-color", "--tail", str(tail)]
        if since:
            args.extend(["--since", since])
        rc, output = await self._compose_cli(args)
        return StackOpResult(
            project=project,
            action="logs",
            ok=rc == 0,
            affected=0,
            output=output,
        )

    async def restart(self, project_id: str) -> StackOpResult:
        """`docker compose -p <project> restart` — leaves the network
        + volumes in place, just bounces each container. Fast (~few
        seconds) compared to a full down/up cycle."""
        project = self.project_for(project_id)
        rc, output = await self._compose_cli(["-p", project, "restart"])
        return StackOpResult(
            project=project,
            action="restart",
            ok=rc == 0,
            affected=0,  # docker compose doesn't print a clean count
            output=output[-4096:],
        )

    async def _compose_cli(self, args: list[str]) -> tuple[int, str]:
        """Spawn `docker compose <args>`. We don't use the docker
        SDK here because `compose` operations are not exposed on
        `DockerClient` — they're a separate plugin binary."""
        cmd = ["docker", "compose", *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            return (-1, f"docker compose not found: {exc}")
        try:
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        except TimeoutError:
            proc.kill()
            return (-1, f"command timed out: {' '.join(cmd)}")
        return (proc.returncode if proc.returncode is not None else -1, out_b.decode("utf-8", "replace"))
