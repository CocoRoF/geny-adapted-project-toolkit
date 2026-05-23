"""`SysboxBackend` — real docker SDK implementation that runs sandboxes
under the `sysbox-runc` runtime.

Design notes:
- Every public method is `async` so the call sites match the protocol,
  but docker-py itself is sync. Each call hops onto a thread pool via
  `asyncio.to_thread` — fine for the M1 cadence (boot rate is low).
- The DockerClient is *injectable* so unit tests can pass a Mock
  without standing up a real daemon. `make_default_client()` is the
  prod factory.
- `validate_mounts` runs at the top of `create` so mount-whitelist
  bypass is impossible without touching the invariant.
- Host docker socket is explicitly *not* added to `volumes` — the
  whitelist guards against accidental adds, and there is no code path
  here that would mount it. The runtime image inside the sandbox runs
  its own inner dockerd (see M0-P2 PoC).

Cycle 1.9 wires the agent-daemon healthcheck loop on top of this
backend — a stub is left at `wait_for_daemon` so the workspace
lifecycle code (Cycle 1.8) can already call into it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from gapt_server.db.ulid import new_ulid
from gapt_server.domains.sandbox.backend import (
    ExecResult,
    SandboxBackendError,
    SandboxCreateSpec,
    SandboxInfo,
    SandboxRef,
    validate_mounts,
)

if TYPE_CHECKING:
    import docker
    from docker.models.containers import Container

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SysboxBackendConfig:
    runtime: str = "sysbox-runc"
    network_mode: str = "bridge"
    # When set, every container gets these labels merged on top of the
    # caller's CreateSpec labels — useful for fleet-wide bookkeeping.
    base_labels: tuple[tuple[str, str], ...] = (
        ("gapt.managed", "true"),
        ("gapt.runtime", "sysbox-runc"),
    )
    # Seconds we wait for the inner agent daemon socket before giving
    # up. Cycle 1.9 fills the actual probe — for now we just expose
    # the timeout.
    daemon_healthcheck_timeout_s: float = 60.0


class SysboxBackend:
    """Production sandbox backend.

    Tests inject a mock via the `client` parameter — see
    `tests/sandbox/test_sysbox_backend.py`. The protocol-level
    `MockSandboxBackend` is preferred when the test only cares about
    workflow ordering; `SysboxBackend` with a Mock is for when the
    test needs to assert the *exact* docker call.
    """

    name = "sysbox"

    def __init__(
        self,
        *,
        client: docker.DockerClient,
        config: SysboxBackendConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or SysboxBackendConfig()

    # ─────────────────────────────────────────────────────────── create ──

    async def create(self, spec: SandboxCreateSpec) -> SandboxRef:
        validate_mounts(spec.mounts)
        sandbox_id = new_ulid()
        kwargs = self._container_kwargs(spec, sandbox_id)
        try:
            container: Container = await asyncio.to_thread(self._client.containers.create, **kwargs)
        except Exception as exc:
            raise SandboxBackendError(f"docker.containers.create failed: {exc}") from exc
        logger.info(
            "sandbox.created",
            sandbox_id=sandbox_id,
            container_id=container.id,
            image=spec.image,
            runtime=self._config.runtime,
        )
        return SandboxRef(id=sandbox_id, container_id=container.id, backend=self.name)

    def _container_kwargs(self, spec: SandboxCreateSpec, sandbox_id: str) -> dict[str, Any]:
        labels: dict[str, str] = {
            **dict(self._config.base_labels),
            "gapt.sandbox_id": sandbox_id,
            "gapt.project_id": spec.project_id,
            **spec.labels,
        }
        if spec.workspace_id is not None:
            labels["gapt.workspace_id"] = spec.workspace_id

        env: dict[str, str] = {
            "GAPT_SANDBOX_ID": sandbox_id,
            "GAPT_PROJECT_ID": spec.project_id,
            **spec.env,
        }
        if spec.workspace_id is not None:
            env["GAPT_WORKSPACE_ID"] = spec.workspace_id

        # docker-py's volumes shape: { host_path: {"bind": container_path, "mode": "ro"|"rw"} }
        volumes: dict[str, dict[str, str]] = {
            mount.source: {
                "bind": mount.target,
                "mode": "ro" if mount.read_only else "rw",
            }
            for mount in spec.mounts
        }

        return {
            "image": spec.image,
            "name": f"gapt-{sandbox_id}",
            "runtime": self._config.runtime,
            "network": self._config.network_mode,
            "labels": labels,
            "environment": env,
            "volumes": volumes or None,
            "detach": True,
            "tty": False,
            "stdin_open": False,
            "cpu_period": spec.resources.cpu_period_us,
            "cpu_quota": spec.resources.cpu_quota_us,
            "mem_limit": spec.resources.mem_bytes,
            "pids_limit": spec.resources.pids_limit,
        }

    # ────────────────────────────────────────────────────── lifecycle ──

    async def start(self, ref: SandboxRef) -> None:
        container = await self._get_container(ref)
        try:
            await asyncio.to_thread(container.start)
        except Exception as exc:
            raise SandboxBackendError(
                f"docker container {ref.container_id} start failed: {exc}"
            ) from exc
        logger.info("sandbox.started", sandbox_id=ref.id, container_id=ref.container_id)

    async def stop(self, ref: SandboxRef) -> None:
        container = await self._get_container(ref)
        try:
            await asyncio.to_thread(container.stop, timeout=10)
        except Exception as exc:
            raise SandboxBackendError(
                f"docker container {ref.container_id} stop failed: {exc}"
            ) from exc
        logger.info("sandbox.stopped", sandbox_id=ref.id)

    async def inspect(self, ref: SandboxRef) -> SandboxInfo:
        container = await self._get_container(ref)
        await asyncio.to_thread(container.reload)
        attrs = container.attrs or {}
        state = attrs.get("State", {}) or {}
        return SandboxInfo(
            ref=ref,
            status=str(state.get("Status", "unknown")),
            image=str(attrs.get("Config", {}).get("Image", "")),
            created_at=time.time(),
        )

    async def destroy(self, ref: SandboxRef) -> None:
        container = await self._get_container(ref)
        try:
            await asyncio.to_thread(container.remove, force=True)
        except Exception as exc:
            raise SandboxBackendError(
                f"docker container {ref.container_id} remove failed: {exc}"
            ) from exc
        logger.info("sandbox.destroyed", sandbox_id=ref.id)

    async def exec_in(self, ref: SandboxRef, argv: list[str]) -> ExecResult:
        container = await self._get_container(ref)
        started = time.monotonic()
        try:
            output = await asyncio.to_thread(
                container.exec_run,
                cmd=argv,
                demux=True,
            )
        except Exception as exc:
            raise SandboxBackendError(f"docker exec_run failed: {exc}") from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)

        exit_code: int = int(output.exit_code) if output.exit_code is not None else -1
        stdout_b, stderr_b = output.output or (b"", b"")
        return ExecResult(
            exit_code=exit_code,
            stdout=stdout_b or b"",
            stderr=stderr_b or b"",
            duration_ms=elapsed_ms,
        )

    # ───────────────────────────────────────────── daemon healthcheck ──

    async def wait_for_daemon(self, ref: SandboxRef) -> None:
        """Block until the in-container agent daemon answers.

        Cycle 1.9 fills the actual probe (unix socket connect + JWT
        round-trip). For now we just confirm the container is in the
        ``running`` state — this gives downstream code a single seam to
        wait on.
        """
        deadline = time.monotonic() + self._config.daemon_healthcheck_timeout_s
        while time.monotonic() < deadline:
            info = await self.inspect(ref)
            if info.status == "running":
                return
            await asyncio.sleep(0.5)
        raise SandboxBackendError(
            f"sandbox {ref.id} did not enter running state within "
            f"{self._config.daemon_healthcheck_timeout_s:.0f}s"
        )

    # ───────────────────────────────────────────── helpers ──

    async def _get_container(self, ref: SandboxRef) -> Container:
        if ref.container_id is None:
            raise SandboxBackendError(f"sandbox {ref.id} has no container_id")
        try:
            return await asyncio.to_thread(self._client.containers.get, ref.container_id)
        except Exception as exc:
            raise SandboxBackendError(
                f"docker container {ref.container_id} not found: {exc}"
            ) from exc


def make_default_client() -> docker.DockerClient:
    """Build a `docker.DockerClient` from the ambient docker env vars.

    Imported lazily so tests that mock at the boundary don't pay the
    docker module import cost.
    """
    import docker as _docker  # noqa: PLC0415  — lazy import is intentional

    return _docker.from_env()
