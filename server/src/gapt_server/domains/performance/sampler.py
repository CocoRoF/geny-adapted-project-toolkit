"""Live docker stats sampler for the Performance dashboard.

The shape of the data we surface:

  * `ContainerSummary` — identity + category + status (cheap; pulled
    from `container.attrs` without an extra API round-trip).
  * `ContainerLimits` — what was *requested* at create time
    (`NanoCpus`, `Memory`, `PidsLimit`, mounts). Static while the
    container is alive.
  * `ContainerStats` — what the container is *using right now*
    (CPU %, memory bytes, network/block IO totals). Computed from
    the single stats sample docker returns when `stream=False`; the
    docker daemon ships the previous sample alongside so CPU delta
    is calculable in one call.

The docker SDK is synchronous → every call is wrapped in
`asyncio.to_thread`. `sample_all()` parallelises across containers
so a host with 20 containers still answers in ~1 s.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import docker
    from docker.models.containers import Container

logger = structlog.get_logger(__name__)


class ContainerCategory(StrEnum):
    """How the operator should *think* about the container."""

    WORKSPACE = "workspace"  # gapt-ws-<wid>
    PROD = "prod"  # gapt-prod-* compose stack
    INFRA = "infra"  # gapt-dev-* (control plane)
    OTHER = "other"  # everything else on the host


@dataclass(frozen=True)
class ContainerSummary:
    id: str
    name: str
    image: str
    category: ContainerCategory
    workspace_id: str | None
    project_id: str | None
    # DB-side enrichments. Populated by `ContainerSampler` from the
    # workspaces / environments tables when a `workspace_id` /
    # `compose_project` matches; None for orphan containers (manual
    # docker runs, archived workspaces, etc.). The dashboard's tree
    # view groups containers by these.
    project_slug: str | None = None
    project_display_name: str | None = None
    workspace_branch: str | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    compose_project: str | None = None
    compose_service: str | None = None
    status: str = "unknown"
    started_at: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class ContainerLimits:
    """Static cgroup ceilings, picked up from `HostConfig`. All
    `None` slots mean *unlimited*."""

    cpu_quota_us: int | None  # microseconds the cgroup may run per period
    cpu_period_us: int | None  # microseconds in one period (default 100_000)
    nano_cpus: int | None  # newer `--cpus` field (1.0 = 1_000_000_000)
    cpus_effective: float | None  # derived: best-effort `--cpus`-equivalent
    mem_bytes: int | None  # `--memory`
    memswap_bytes: int | None  # `--memory-swap`
    pids_limit: int | None
    runtime: str  # `runc` / `sysbox-runc` / …
    network_mode: str
    networks: list[str] = field(default_factory=list)
    mount_count: int = 0


@dataclass(frozen=True)
class ContainerStats:
    """Live measurement at sample time."""

    cpu_pct: float  # 0..(cpus*100); >100% on multi-CPU hosts is normal
    online_cpus: int
    mem_bytes: int
    mem_limit_bytes: int | None
    mem_pct: float  # 0..100, against the cgroup limit (or host memory if unlimited)
    net_rx_bytes: int
    net_tx_bytes: int
    block_rx_bytes: int
    block_tx_bytes: int
    pids: int | None


@dataclass(frozen=True)
class ContainerSample:
    """Joined record returned by the API."""

    summary: ContainerSummary
    limits: ContainerLimits
    stats: ContainerStats | None  # None when the container isn't running


# ─────────────────────────────────────────────────────────── helpers ──


_GAPT_NAME_PREFIXES = ("gapt-ws-", "gapt-prod-", "gapt-dev-", "gapt-")


def _classify(attrs: dict[str, Any]) -> ContainerCategory:
    labels = (attrs.get("Config") or {}).get("Labels") or {}
    name = (attrs.get("Name") or "").lstrip("/")
    if "gapt.workspace_id" in labels:
        return ContainerCategory.WORKSPACE
    compose_project = labels.get("com.docker.compose.project") or ""
    if compose_project.startswith("gapt-prod-"):
        return ContainerCategory.PROD
    if name.startswith("gapt-ws-"):
        return ContainerCategory.WORKSPACE
    if name.startswith("gapt-prod-") or "gapt-prod-" in compose_project:
        return ContainerCategory.PROD
    if compose_project == "gapt-dev" or name.startswith("gapt-dev-"):
        return ContainerCategory.INFRA
    return ContainerCategory.OTHER


def _summary_from(attrs: dict[str, Any]) -> ContainerSummary:
    labels = (attrs.get("Config") or {}).get("Labels") or {}
    state = attrs.get("State") or {}
    compose_project = labels.get("com.docker.compose.project")
    name = (attrs.get("Name") or "").lstrip("/")
    workspace_id = labels.get("gapt.workspace_id")
    # Sandbox containers (`gapt-<ULID>`) don't carry a workspace_id
    # label, but the ULID after the prefix _is_ the workspace id (the
    # SandboxBackend creates the container name from `gapt-<sandbox_id>`
    # where sandbox_id ≠ workspace_id; for workspace-row matching we
    # rely on the `gapt-ws-<wid>` containers which DO have the label).
    if workspace_id is None and name.startswith("gapt-ws-"):
        workspace_id = name[len("gapt-ws-") :].upper()
    # Prod compose projects are named `gapt-prod-<project_id>` (see
    # `domains/deploy/local.py::_compose_project` + the docstring on
    # `StackManager` at lines 78–84 — same project's envs share one
    # stack name keyed on project_id, NOT environment_id).
    #
    # BUG FIX (Phase N.2.7): this code used to call the extracted ID
    # `environment_id`, then the Performance dashboard tried to look
    # it up in the environments table and (correctly) failed, marking
    # every real prod stack as ORPHAN. The fix: trust the deploy
    # layer's convention — what comes out is the project_id. We don't
    # try to infer environment_id from the container at all (there's
    # no label carrying it today; multi-env-per-project isn't yet a
    # thing the deploy stack supports anyway).
    project_id = labels.get("gapt.project_id")
    if (
        project_id is None
        and compose_project
        and compose_project.startswith("gapt-prod-")
    ):
        project_id = compose_project[len("gapt-prod-") :].upper()
    return ContainerSummary(
        id=attrs.get("Id", ""),
        name=name,
        image=(attrs.get("Config") or {}).get("Image", ""),
        category=_classify(attrs),
        workspace_id=workspace_id,
        project_id=project_id,
        environment_id=None,
        compose_project=compose_project,
        compose_service=labels.get("com.docker.compose.service"),
        status=state.get("Status", "unknown"),
        started_at=state.get("StartedAt"),
        created_at=attrs.get("Created"),
    )


def _limits_from(attrs: dict[str, Any]) -> ContainerLimits:
    host = attrs.get("HostConfig") or {}
    networks = ((attrs.get("NetworkSettings") or {}).get("Networks") or {}).keys()
    nano_cpus = host.get("NanoCpus") or None
    cpu_quota = host.get("CpuQuota") or None
    cpu_period = host.get("CpuPeriod") or None
    if nano_cpus:
        cpus_eff: float | None = nano_cpus / 1_000_000_000
    elif cpu_quota and cpu_period:
        cpus_eff = cpu_quota / cpu_period
    else:
        cpus_eff = None
    mem = host.get("Memory") or 0
    memswap = host.get("MemorySwap") or 0
    return ContainerLimits(
        cpu_quota_us=cpu_quota or None,
        cpu_period_us=cpu_period or None,
        nano_cpus=nano_cpus,
        cpus_effective=cpus_eff,
        mem_bytes=mem or None,
        memswap_bytes=memswap or None,
        pids_limit=host.get("PidsLimit") or None,
        runtime=host.get("Runtime", "runc"),
        network_mode=host.get("NetworkMode", ""),
        networks=list(networks),
        mount_count=len(host.get("Mounts") or attrs.get("Mounts") or []),
    )


def _cpu_pct(sample: dict[str, Any]) -> tuple[float, int]:
    """Returns (cpu%, online_cpus). Docker provides `precpu_stats` in
    the same sample so a one-shot `stats(stream=False)` gives us
    everything we need."""
    cpu = sample.get("cpu_stats", {}) or {}
    pre = sample.get("precpu_stats", {}) or {}
    cpu_total = (cpu.get("cpu_usage") or {}).get("total_usage", 0)
    pre_total = (pre.get("cpu_usage") or {}).get("total_usage", 0)
    cpu_sys = cpu.get("system_cpu_usage", 0)
    pre_sys = pre.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len(
        (cpu.get("cpu_usage") or {}).get("percpu_usage") or []
    ) or 1
    cpu_delta = cpu_total - pre_total
    sys_delta = cpu_sys - pre_sys
    if sys_delta <= 0 or cpu_delta < 0:
        return (0.0, online)
    return ((cpu_delta / sys_delta) * online * 100.0, online)


def _mem_bytes(sample: dict[str, Any]) -> tuple[int, int | None]:
    mem = sample.get("memory_stats") or {}
    usage = int(mem.get("usage", 0) or 0)
    stats = mem.get("stats") or {}
    # cgroup v2 reports `inactive_file`; v1 reports `cache`. Subtract
    # so we report "working set" — matches `docker stats` output.
    drop = int(stats.get("inactive_file") or stats.get("cache") or 0)
    working = max(usage - drop, 0)
    limit = int(mem.get("limit") or 0) or None
    return (working, limit)


def _net_totals(sample: dict[str, Any]) -> tuple[int, int]:
    rx, tx = 0, 0
    for iface in (sample.get("networks") or {}).values():
        rx += int(iface.get("rx_bytes", 0) or 0)
        tx += int(iface.get("tx_bytes", 0) or 0)
    return (rx, tx)


def _block_totals(sample: dict[str, Any]) -> tuple[int, int]:
    rx, tx = 0, 0
    for row in (sample.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []:
        op = (row.get("op") or "").lower()
        v = int(row.get("value", 0) or 0)
        if op in {"read", "rx"}:
            rx += v
        elif op in {"write", "tx"}:
            tx += v
    return (rx, tx)


def _stats_from(sample: dict[str, Any]) -> ContainerStats:
    cpu_pct, online = _cpu_pct(sample)
    mem_bytes, mem_limit = _mem_bytes(sample)
    mem_pct = 0.0
    if mem_limit:
        mem_pct = (mem_bytes / mem_limit) * 100.0
    net_rx, net_tx = _net_totals(sample)
    blk_rx, blk_tx = _block_totals(sample)
    pids_stats = sample.get("pids_stats") or {}
    pids = pids_stats.get("current")
    return ContainerStats(
        cpu_pct=cpu_pct,
        online_cpus=online,
        mem_bytes=mem_bytes,
        mem_limit_bytes=mem_limit,
        mem_pct=mem_pct,
        net_rx_bytes=net_rx,
        net_tx_bytes=net_tx,
        block_rx_bytes=blk_rx,
        block_tx_bytes=blk_tx,
        pids=pids,
    )


def _is_gapt_container(attrs: dict[str, Any]) -> bool:
    """Filter out random host containers (`new-web-*`, etc.) — we only
    want things this GAPT instance is responsible for. Best-effort:
    a label match wins, otherwise the conventional name prefixes."""
    labels = (attrs.get("Config") or {}).get("Labels") or {}
    if "gapt.workspace_id" in labels or "gapt.project_id" in labels:
        return True
    compose = labels.get("com.docker.compose.project") or ""
    if compose == "gapt-dev" or compose.startswith("gapt-prod-"):
        return True
    name = (attrs.get("Name") or "").lstrip("/")
    return any(name.startswith(p) for p in _GAPT_NAME_PREFIXES)


# ───────────────────────────────────────────────────────── sampler ──


class ContainerSampler:
    """Wraps a `docker.DockerClient` with the bookkeeping the API
    routes care about. Construct once (the daemon socket open is
    expensive); call `sample_all` per request.

    `sample_all()` is wrapped in a ~2 s TTL cache + single-flight
    lock. Why both:

      * **TTL cache**: docker `stats(stream=False)` blocks ~1 s per
        container on the host's /proc deltas. Sampling 20+ containers
        without a cache means the browser's 3 s poll cycle never
        catches up, and concurrent browser tabs each pay the full
        cost. A 2 s ceiling is fresh enough that the dashboard still
        feels live and bounds the worst-case load.

      * **Single-flight lock**: when N tabs poll simultaneously
        and the cache happens to be expired, only ONE of them
        does the real sampling; the others await the in-flight
        coroutine and get the result for free. Otherwise we'd
        still saturate the docker daemon on cache misses.
    """

    _SAMPLE_TTL_S = 2.0

    def __init__(self, client: docker.DockerClient) -> None:
        self._client = client
        self._sample_cache: list[ContainerSample] | None = None
        self._sample_cache_at: float = 0.0
        self._sample_lock = asyncio.Lock()

    async def sample_all(self) -> list[ContainerSample]:
        now = time.monotonic()
        if (
            self._sample_cache is not None
            and (now - self._sample_cache_at) < self._SAMPLE_TTL_S
        ):
            return self._sample_cache
        async with self._sample_lock:
            # Another caller may have refilled the cache while we
            # waited on the lock; re-check before doing work.
            now = time.monotonic()
            if (
                self._sample_cache is not None
                and (now - self._sample_cache_at) < self._SAMPLE_TTL_S
            ):
                return self._sample_cache
            out = await self._sample_all_uncached()
            self._sample_cache = out
            self._sample_cache_at = time.monotonic()
            return out

    async def _sample_all_uncached(self) -> list[ContainerSample]:
        attrs_list = await asyncio.to_thread(self._list_attrs)
        gapt = [a for a in attrs_list if _is_gapt_container(a)]
        # Stats calls are I/O-bound; parallelise.
        results = await asyncio.gather(
            *(self._sample_one(a) for a in gapt), return_exceptions=True
        )
        out: list[ContainerSample] = []
        for r in results:
            if isinstance(r, ContainerSample):
                out.append(r)
        # Stable sort: workspaces first, then prod, then infra,
        # tie-broken by name so the operator sees consistent order.
        order = {
            ContainerCategory.WORKSPACE: 0,
            ContainerCategory.PROD: 1,
            ContainerCategory.INFRA: 2,
            ContainerCategory.OTHER: 3,
        }
        out.sort(key=lambda s: (order[s.summary.category], s.summary.name))
        return out

    async def sample_one(self, container_id: str) -> ContainerSample | None:
        try:
            container = await asyncio.to_thread(self._client.containers.get, container_id)
        except Exception:  # noqa: BLE001
            return None
        attrs = container.attrs
        if not _is_gapt_container(attrs):
            return None
        return await self._sample_one(attrs)

    async def stop(self, container_id: str, *, timeout_s: int = 10) -> None:
        """Graceful SIGTERM + grace period, then SIGKILL. Idempotent
        on already-stopped containers (the docker daemon no-ops)."""
        c = await self._get(container_id)
        if c is None:
            raise LookupError(container_id)
        await asyncio.to_thread(c.stop, timeout=timeout_s)

    async def kill(self, container_id: str, *, signal: str = "SIGKILL") -> None:
        c = await self._get(container_id)
        if c is None:
            raise LookupError(container_id)
        await asyncio.to_thread(c.kill, signal=signal)

    async def restart(self, container_id: str, *, timeout_s: int = 10) -> None:
        c = await self._get(container_id)
        if c is None:
            raise LookupError(container_id)
        await asyncio.to_thread(c.restart, timeout=timeout_s)

    async def logs(
        self,
        container_id: str,
        *,
        tail: int = 200,
        timestamps: bool = True,
    ) -> str:
        c = await self._get(container_id)
        if c is None:
            raise LookupError(container_id)
        raw = await asyncio.to_thread(
            c.logs, tail=tail, timestamps=timestamps, stdout=True, stderr=True
        )
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    async def _get(self, container_id: str) -> Container | None:
        try:
            c = await asyncio.to_thread(self._client.containers.get, container_id)
        except Exception:  # noqa: BLE001
            return None
        if not _is_gapt_container(c.attrs):
            return None
        return c

    def _list_attrs(self) -> list[dict[str, Any]]:
        # Include stopped containers so the operator sees what's
        # currently *configured* even when it's not running. `attrs`
        # carries `HostConfig` (limits) even on exited containers.
        containers: list[Container] = self._client.containers.list(all=True)
        return [c.attrs for c in containers]

    async def _sample_one(self, attrs: dict[str, Any]) -> ContainerSample:
        summary = _summary_from(attrs)
        limits = _limits_from(attrs)
        stats: ContainerStats | None = None
        if summary.status == "running":
            try:
                sample = await asyncio.to_thread(self._stats_once, attrs["Id"])
                stats = _stats_from(sample)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "performance.stats_failed",
                    container_id=attrs.get("Id", "")[:12],
                    error=str(exc),
                )
        return ContainerSample(summary=summary, limits=limits, stats=stats)

    def _stats_once(self, container_id: str) -> dict[str, Any]:
        # Docker's `stats(stream=False)` blocks until two samples
        # are taken (~1 s), then returns a dict with both. We use
        # `decode=True` to get a parsed dict directly.
        c = self._client.containers.get(container_id)
        sample = c.stats(stream=False, decode=False)
        if isinstance(sample, dict):
            return sample
        return {}  # pragma: no cover — defensive
