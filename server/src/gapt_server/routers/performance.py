"""`/api/performance` — live container stats + resource limits + GPU.

Endpoints:

  * `GET    /api/performance/containers`            snapshot of every
    GAPT-related container with limits + live stats + project /
    workspace / environment enrichment from the DB.
  * `GET    /api/performance/containers/{id}`       single-container
    detail.
  * `POST   /api/performance/containers/{id}/stop`  graceful SIGTERM,
    grace period, then SIGKILL.
  * `POST   /api/performance/containers/{id}/kill`  immediate SIGKILL.
  * `POST   /api/performance/containers/{id}/restart` restart.
  * `GET    /api/performance/containers/{id}/logs`  text logs (tail).
  * `GET    /api/performance/host`                  host info.
  * `GET    /api/performance/gpu`                   GPU stats (empty
    list when no NVIDIA driver is present).

All endpoints require auth. The stats endpoint joins against the DB
so the dashboard can render a Project → Workspace → Container tree
without a second round-trip per container.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gapt_server.container import AppContainer, get_container
from gapt_server.db import models
from gapt_server.domains.auth import AdminPrincipal
from gapt_server.domains.performance import (
    ContainerCategory,
    ContainerLimits,
    ContainerSample,
    ContainerSampler,
    ContainerStats,
    ContainerSummary,
    GpuSample,
    sample_gpu,
)
from gapt_server.domains.sandbox import make_default_client
from gapt_server.routers.auth import get_current_user

router = APIRouter(prefix="/api/performance", tags=["performance"])

# Module-level singleton — the docker client open is non-trivial
# (unix socket + version negotiation), don't redo it per request.
_SAMPLER: ContainerSampler | None = None


def _get_sampler() -> ContainerSampler:
    global _SAMPLER  # noqa: PLW0603
    if _SAMPLER is None:
        _SAMPLER = ContainerSampler(client=make_default_client())
    return _SAMPLER


# ────────────────────────────────────────────────── response models ──


class SummaryDto(BaseModel):
    id: str
    name: str
    image: str
    category: str
    workspace_id: str | None
    project_id: str | None
    project_slug: str | None
    project_display_name: str | None
    workspace_branch: str | None
    environment_id: str | None
    environment_name: str | None
    compose_project: str | None
    compose_service: str | None
    status: str
    started_at: str | None
    created_at: str | None


class LimitsDto(BaseModel):
    cpu_quota_us: int | None
    cpu_period_us: int | None
    nano_cpus: int | None
    cpus_effective: float | None
    mem_bytes: int | None
    memswap_bytes: int | None
    pids_limit: int | None
    runtime: str
    network_mode: str
    networks: list[str]
    mount_count: int


class StatsDto(BaseModel):
    cpu_pct: float
    online_cpus: int
    mem_bytes: int
    mem_limit_bytes: int | None
    mem_pct: float
    net_rx_bytes: int
    net_tx_bytes: int
    block_rx_bytes: int
    block_tx_bytes: int
    pids: int | None


class SampleDto(BaseModel):
    summary: SummaryDto
    limits: LimitsDto
    stats: StatsDto | None


class ProjectDto(BaseModel):
    id: str
    slug: str
    display_name: str


class WorkspaceDto(BaseModel):
    id: str
    project_id: str
    branch: str
    status: str


class EnvironmentDto(BaseModel):
    id: str
    project_id: str
    name: str


class ContainersResponse(BaseModel):
    samples: list[SampleDto]
    projects: list[ProjectDto]
    workspaces: list[WorkspaceDto]
    environments: list[EnvironmentDto]
    total_containers: int
    running_containers: int
    total_cpu_pct: float
    total_mem_bytes: int


class HostInfoResponse(BaseModel):
    cpus: int
    mem_total_bytes: int
    docker_version: str
    runtime: str


class GpuDto(BaseModel):
    index: int
    name: str
    driver_version: str
    utilization_pct: float
    memory_used_bytes: int
    memory_total_bytes: int
    memory_pct: float
    temperature_c: float | None
    power_watts: float | None


class GpusResponse(BaseModel):
    available: bool
    gpus: list[GpuDto]


class LogsResponse(BaseModel):
    container_id: str
    text: str
    truncated_to_tail: int


class ActionResponse(BaseModel):
    container_id: str
    action: str
    ok: bool = True


# ─────────────────────────────────────────────── DB enrichment ──


async def _load_db_context(
    db: AsyncSession,
) -> tuple[
    dict[str, models.Project],
    dict[str, models.Workspace],
    dict[str, models.Environment],
]:
    """Load the projects / workspaces / environments tables once per
    request. The samples loop joins against these dicts."""
    projects_rows = (await db.execute(select(models.Project))).scalars().all()
    workspaces_rows = (await db.execute(select(models.Workspace))).scalars().all()
    envs_rows = (await db.execute(select(models.Environment))).scalars().all()
    return (
        {p.id: p for p in projects_rows},
        {w.id: w for w in workspaces_rows},
        {e.id: e for e in envs_rows},
    )


def _enrich_summary(
    summary: ContainerSummary,
    projects: dict[str, models.Project],
    workspaces: dict[str, models.Workspace],
    environments: dict[str, models.Environment],
) -> ContainerSummary:
    """Fill `project_*` / `workspace_branch` / `environment_name` by
    looking up the DB rows that match the container's labels /
    derived IDs."""
    project_id = summary.project_id
    workspace_branch = summary.workspace_branch
    project_slug = summary.project_slug
    project_display_name = summary.project_display_name
    environment_name = summary.environment_name

    if summary.workspace_id and summary.workspace_id in workspaces:
        ws = workspaces[summary.workspace_id]
        workspace_branch = ws.branch
        project_id = project_id or ws.project_id

    if summary.environment_id and summary.environment_id in environments:
        env = environments[summary.environment_id]
        environment_name = env.name
        project_id = project_id or env.project_id

    if project_id and project_id in projects:
        proj = projects[project_id]
        project_slug = proj.slug
        project_display_name = proj.display_name

    return ContainerSummary(
        id=summary.id,
        name=summary.name,
        image=summary.image,
        category=summary.category,
        workspace_id=summary.workspace_id,
        project_id=project_id,
        project_slug=project_slug,
        project_display_name=project_display_name,
        workspace_branch=workspace_branch,
        environment_id=summary.environment_id,
        environment_name=environment_name,
        compose_project=summary.compose_project,
        compose_service=summary.compose_service,
        status=summary.status,
        started_at=summary.started_at,
        created_at=summary.created_at,
    )


def _summary_dto(s: ContainerSummary) -> SummaryDto:
    return SummaryDto(
        id=s.id,
        name=s.name,
        image=s.image,
        category=s.category.value,
        workspace_id=s.workspace_id,
        project_id=s.project_id,
        project_slug=s.project_slug,
        project_display_name=s.project_display_name,
        workspace_branch=s.workspace_branch,
        environment_id=s.environment_id,
        environment_name=s.environment_name,
        compose_project=s.compose_project,
        compose_service=s.compose_service,
        status=s.status,
        started_at=s.started_at,
        created_at=s.created_at,
    )


def _limits_dto(l: ContainerLimits) -> LimitsDto:
    return LimitsDto(
        cpu_quota_us=l.cpu_quota_us,
        cpu_period_us=l.cpu_period_us,
        nano_cpus=l.nano_cpus,
        cpus_effective=l.cpus_effective,
        mem_bytes=l.mem_bytes,
        memswap_bytes=l.memswap_bytes,
        pids_limit=l.pids_limit,
        runtime=l.runtime,
        network_mode=l.network_mode,
        networks=l.networks,
        mount_count=l.mount_count,
    )


def _stats_dto(s: ContainerStats | None) -> StatsDto | None:
    if s is None:
        return None
    return StatsDto(
        cpu_pct=s.cpu_pct,
        online_cpus=s.online_cpus,
        mem_bytes=s.mem_bytes,
        mem_limit_bytes=s.mem_limit_bytes,
        mem_pct=s.mem_pct,
        net_rx_bytes=s.net_rx_bytes,
        net_tx_bytes=s.net_tx_bytes,
        block_rx_bytes=s.block_rx_bytes,
        block_tx_bytes=s.block_tx_bytes,
        pids=s.pids,
    )


def _sample_dto(s: ContainerSample) -> SampleDto:
    return SampleDto(
        summary=_summary_dto(s.summary),
        limits=_limits_dto(s.limits),
        stats=_stats_dto(s.stats),
    )


def _gpu_dto(g: GpuSample) -> GpuDto:
    return GpuDto(
        index=g.index,
        name=g.name,
        driver_version=g.driver_version,
        utilization_pct=g.utilization_pct,
        memory_used_bytes=g.memory_used_bytes,
        memory_total_bytes=g.memory_total_bytes,
        memory_pct=g.memory_pct,
        temperature_c=g.temperature_c,
        power_watts=g.power_watts,
    )


# ────────────────────────────────────────────────────── endpoints ──


async def _load_db_context_isolated(
    container: AppContainer,
) -> tuple[
    dict[str, models.Project],
    dict[str, models.Workspace],
    dict[str, models.Environment],
]:
    """Open a short-lived session JUST for the projects/workspaces/
    environments lookup, release the connection immediately. The
    performance endpoints are polled at 3 s while the actual stats
    sampling takes ~1 s of docker I/O — if we held the request-
    scoped session for the whole duration we'd saturate the pool
    when multiple browser tabs poll concurrently. Two empty tables
    + a sub-50ms read does not deserve a long-lived connection."""
    if container.session_factory is None:
        return ({}, {}, {})
    async with container.session_factory() as db:
        return await _load_db_context(db)


@router.get("/containers", response_model=ContainersResponse)
async def list_containers(
    container: AppContainer = Depends(get_container),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ContainersResponse:
    sampler = _get_sampler()
    # DB read uses its own short-lived session (released before the
    # slow docker sampling begins / in parallel with it). The
    # request-scoped session dependency is intentionally NOT used.
    samples, db_ctx = await asyncio.gather(
        sampler.sample_all(),
        _load_db_context_isolated(container),
    )
    projects, workspaces, environments = db_ctx
    enriched: list[ContainerSample] = []
    for s in samples:
        enriched.append(
            ContainerSample(
                summary=_enrich_summary(s.summary, projects, workspaces, environments),
                limits=s.limits,
                stats=s.stats,
            )
        )
    total_cpu = sum((s.stats.cpu_pct for s in enriched if s.stats), 0.0)
    total_mem = sum((s.stats.mem_bytes for s in enriched if s.stats), 0)
    running = sum(1 for s in enriched if s.summary.status == "running")
    return ContainersResponse(
        samples=[_sample_dto(s) for s in enriched],
        projects=[
            ProjectDto(id=p.id, slug=p.slug, display_name=p.display_name)
            for p in projects.values()
        ],
        workspaces=[
            WorkspaceDto(
                id=w.id,
                project_id=w.project_id,
                branch=w.branch,
                status=w.status.value,
            )
            for w in workspaces.values()
        ],
        environments=[
            EnvironmentDto(id=e.id, project_id=e.project_id, name=e.name)
            for e in environments.values()
        ],
        total_containers=len(enriched),
        running_containers=running,
        total_cpu_pct=total_cpu,
        total_mem_bytes=total_mem,
    )


@router.get("/containers/{container_id}", response_model=SampleDto)
async def get_container_detail(
    container_id: str,
    container: AppContainer = Depends(get_container),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> SampleDto:
    sampler = _get_sampler()
    sample = await sampler.sample_one(container_id)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "container.not_found", "reason": container_id},
        )
    projects, workspaces, environments = await _load_db_context_isolated(container)
    enriched = ContainerSample(
        summary=_enrich_summary(sample.summary, projects, workspaces, environments),
        limits=sample.limits,
        stats=sample.stats,
    )
    return _sample_dto(enriched)


@router.post("/containers/{container_id}/stop", response_model=ActionResponse)
async def stop_container(
    container_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ActionResponse:
    sampler = _get_sampler()
    try:
        await sampler.stop(container_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "container.not_found", "reason": container_id},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "container.stop_failed", "reason": str(exc)},
        ) from exc
    return ActionResponse(container_id=container_id, action="stop")


@router.post("/containers/{container_id}/kill", response_model=ActionResponse)
async def kill_container(
    container_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ActionResponse:
    sampler = _get_sampler()
    try:
        await sampler.kill(container_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "container.not_found", "reason": container_id},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "container.kill_failed", "reason": str(exc)},
        ) from exc
    return ActionResponse(container_id=container_id, action="kill")


@router.post("/containers/{container_id}/restart", response_model=ActionResponse)
async def restart_container(
    container_id: str,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ActionResponse:
    sampler = _get_sampler()
    try:
        await sampler.restart(container_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "container.not_found", "reason": container_id},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "container.restart_failed", "reason": str(exc)},
        ) from exc
    return ActionResponse(container_id=container_id, action="restart")


@router.get("/containers/{container_id}/logs", response_model=LogsResponse)
async def container_logs(
    container_id: str,
    tail: int = 200,
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> LogsResponse:
    tail = max(10, min(tail, 5000))
    sampler = _get_sampler()
    try:
        text = await sampler.logs(container_id, tail=tail)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "container.not_found", "reason": container_id},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "container.logs_failed", "reason": str(exc)},
        ) from exc
    return LogsResponse(container_id=container_id, text=text, truncated_to_tail=tail)


@router.get("/host", response_model=HostInfoResponse)
async def host_info(
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> HostInfoResponse:
    sampler = _get_sampler()

    def _info() -> dict[str, Any]:
        return sampler._client.info()  # noqa: SLF001 — internal access intentional

    def _version() -> dict[str, Any]:
        return sampler._client.version()  # noqa: SLF001

    info, ver = await asyncio.gather(
        asyncio.to_thread(_info), asyncio.to_thread(_version)
    )
    return HostInfoResponse(
        cpus=int(info.get("NCPU") or 0),
        mem_total_bytes=int(info.get("MemTotal") or 0),
        docker_version=str(ver.get("Version") or ""),
        runtime=str(info.get("DefaultRuntime") or "runc"),
    )


@router.get("/gpu", response_model=GpusResponse)
async def gpu_info(
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> GpusResponse:
    gpus = await sample_gpu()
    return GpusResponse(available=bool(gpus), gpus=[_gpu_dto(g) for g in gpus])
