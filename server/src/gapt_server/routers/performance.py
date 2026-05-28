"""`/_gapt/api/performance` — live container stats + resource limits + GPU.

Endpoints:

  * `GET    /_gapt/api/performance/containers`            snapshot of every
    GAPT-related container with limits + live stats + project /
    workspace / environment enrichment from the DB.
  * `GET    /_gapt/api/performance/containers/{id}`       single-container
    detail.
  * `POST   /_gapt/api/performance/containers/{id}/stop`  graceful SIGTERM,
    grace period, then SIGKILL.
  * `POST   /_gapt/api/performance/containers/{id}/kill`  immediate SIGKILL.
  * `POST   /_gapt/api/performance/containers/{id}/restart` restart.
  * `GET    /_gapt/api/performance/containers/{id}/logs`  text logs (tail).
  * `GET    /_gapt/api/performance/host`                  host info.
  * `GET    /_gapt/api/performance/gpu`                   GPU stats (empty
    list when no NVIDIA driver is present).

All endpoints require auth. The stats endpoint joins against the DB
so the dashboard can render a Project → Workspace → Container tree
without a second round-trip per container.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from gapt_server.container import AppContainer, get_app_settings, get_container
from gapt_server.db import enums, models
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
from gapt_server.domains.caddy.admin_api import CaddyAdminClient
from gapt_server.domains.caddy.subdomain import SubdomainManager
from gapt_server.domains.performance.broadcaster import PerformanceBroadcaster
from gapt_server.domains.performance.cleanup import (
    OrphanPlan,
    build_plan as build_orphan_plan,
    execute_cleanup,
)
from gapt_server.domains.sandbox import make_default_client
from gapt_server.routers.auth import get_current_user
from gapt_server.settings import Settings  # noqa: TC001

router = APIRouter(prefix="/_gapt/api/performance", tags=["performance"])

# Module-level singleton — the docker client open is non-trivial
# (unix socket + version negotiation), don't redo it per request.
_SAMPLER: ContainerSampler | None = None


_BROADCASTER: PerformanceBroadcaster | None = None


def _get_sampler() -> ContainerSampler:
    global _SAMPLER  # noqa: PLW0603
    if _SAMPLER is None:
        _SAMPLER = ContainerSampler(client=make_default_client())
    return _SAMPLER


def _get_broadcaster(container: AppContainer) -> PerformanceBroadcaster:
    """Lazy-construct the broadcaster on first stream subscribe. We
    can't wire this at app start because the payload builder closes
    over `container.session_factory`, which isn't valid before the
    DB engine is up."""
    global _BROADCASTER  # noqa: PLW0603
    if _BROADCASTER is None:
        broadcaster = PerformanceBroadcaster(sampler=_get_sampler())

        async def _build_payload(samples: list[ContainerSample]) -> dict:
            # Phase E.2 — fan out three independent fetches in
            # parallel: DB context (incl. session metrics), last
            # deploys, host GPU sample. None of them depends on the
            # others; gathering them halves wall-clock latency.
            db_ctx, last_deploys, gpu_samples = await asyncio.gather(
                _load_db_context_isolated(container),
                _load_last_deploys(container),
                sample_gpu(),
            )
            projects, workspaces, environments, session_metrics = db_ctx
            svc_by_workspace = _snapshot_workspace_services(container)
            enriched: list[ContainerSample] = []
            for s in samples:
                enriched.append(
                    ContainerSample(
                        summary=_enrich_summary(
                            s.summary, projects, workspaces, environments
                        ),
                        limits=s.limits,
                        stats=s.stats,
                    )
                )
            total_cpu = sum((s.stats.cpu_pct for s in enriched if s.stats), 0.0)
            total_mem = sum((s.stats.mem_bytes for s in enriched if s.stats), 0)
            running = sum(1 for s in enriched if s.summary.status == "running")
            from gapt_server.domains.workspace_sandbox.manager import (  # noqa: PLC0415
                format_gpus_flag,
            )

            return {
                "samples": [
                    _sample_dto_with_metrics(s, session_metrics).model_dump()
                    for s in enriched
                ],
                "projects": [
                    {
                        "id": p.id,
                        "slug": p.slug,
                        "display_name": p.display_name,
                        "archived_at": (
                            p.archived_at.isoformat() if p.archived_at else None
                        ),
                    }
                    for p in projects.values()
                ],
                "workspaces": [
                    w.model_dump()
                    for w in _build_workspace_dtos(workspaces, svc_by_workspace)
                ],
                "environments": [
                    e.model_dump() for e in _build_env_dtos(environments, last_deploys)
                ],
                "total_containers": len(enriched),
                "running_containers": running,
                "total_cpu_pct": total_cpu,
                "total_mem_bytes": total_mem,
                "gpus": [_gpu_dto(g).model_dump() for g in gpu_samples],
                "applied_gpu_policy": format_gpus_flag(
                    container.settings.workspace_gpus
                ),
            }

        broadcaster.set_payload_builder(_build_payload)
        _BROADCASTER = broadcaster
    return _BROADCASTER


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


class SessionMetricsDto(BaseModel):
    """Phase E.2 — agent session counters joined onto a container
    sample by `workspace_id`. Sums every non-archived AgentSession
    for that workspace so the perf tab can show "this workspace
    has cost $X / used N tokens" without a second round-trip.

    Always omitted (set on `SampleDto.session_metrics = None`) for
    infrastructure containers (caddy / postgres / prometheus) since
    they don't host agent sessions."""

    cost_usd_total: float
    input_tokens_total: int
    output_tokens_total: int
    session_count: int


class SampleDto(BaseModel):
    summary: SummaryDto
    limits: LimitsDto
    stats: StatsDto | None
    session_metrics: SessionMetricsDto | None = None


class ProjectDto(BaseModel):
    id: str
    slug: str
    display_name: str
    # ISO timestamp when the project was archived (or None if still
    # live). Lets the frontend visually downrank archived projects +
    # offer a cleanup CTA without filtering them out entirely (which
    # would lose the human-readable name for orphan containers).
    archived_at: str | None = None


class WorkspaceServiceDto(BaseModel):
    """A managed dev-server process running INSIDE a workspace
    container. These don't show up as separate docker rows (they
    run via `docker exec` inside the workspace), so without this
    field the Performance dashboard can't tell whether a workspace
    is actively serving anything."""

    label: str
    cmd: str
    port: int | None
    auto_port: int | None
    state: str
    bound_url: str | None


class WorkspaceDto(BaseModel):
    id: str
    project_id: str
    branch: str
    status: str
    services: list[WorkspaceServiceDto] = []


class EnvironmentDto(BaseModel):
    id: str
    project_id: str
    name: str
    # Latest DeployRun snapshot — lets the dashboard render a
    # placeholder for environments whose stack is currently down so
    # the operator knows the env exists + when it was last touched.
    last_deploy_status: str | None = None
    last_deploy_at: str | None = None
    last_deploy_version: str | None = None
    last_bound_url: str | None = None


class ContainersResponse(BaseModel):
    samples: list[SampleDto]
    projects: list[ProjectDto]
    workspaces: list[WorkspaceDto]
    environments: list[EnvironmentDto]
    total_containers: int
    running_containers: int
    total_cpu_pct: float
    total_mem_bytes: int
    # Phase E.2 — live host GPU samples folded into the same stream
    # as the container list. Empty array when no NVIDIA driver is
    # present. The dashboard no longer needs a separate `/gpu` fetch.
    gpus: list[GpuDto] = []
    # Phase E.2 — current `--gpus` value applied to new workspace
    # containers. `null` = CPU-only. Lets the perf tab show "GPU
    # device=0,1 — applies to new workspaces" without polling /gpu.
    applied_gpu_policy: str | None = None


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
    # Phase E.1 — surface the currently-applied workspace GPU policy
    # so the UI can show "containers are getting GPU X" without a
    # second round-trip. `null` means workspace containers boot
    # CPU-only (the default). When non-null, this is exactly what
    # gets passed to `docker run --gpus <value>` in normalised form.
    applied_policy: str | None = None
    # The env var name an operator should set to change the policy.
    # Hard-coded here so the Settings UI can render a copy-paste
    # hint without us re-stating the convention in every locale.
    policy_env_var: str = "GAPT_WORKSPACE_GPUS"


class LogsResponse(BaseModel):
    container_id: str
    text: str
    truncated_to_tail: int


class ActionResponse(BaseModel):
    container_id: str
    action: str
    ok: bool = True


class OrphanTargetDto(BaseModel):
    container_id: str
    container_name: str
    category: str
    workspace_id: str | None
    environment_id: str | None
    worktree_path: str | None
    status: str


class ArchivedProjectPurgeDto(BaseModel):
    project_id: str
    display_name: str
    cascade_workspaces: int
    cascade_environments: int
    cascade_deploy_runs: int


class OrphanPlanResponse(BaseModel):
    containers: list[OrphanTargetDto]
    caddy_route_ids: list[str]
    worktree_paths: list[str]
    archived_projects: list[ArchivedProjectPurgeDto] = []


class CleanupOutcomeDto(BaseModel):
    container_id: str
    container_name: str
    ok: bool
    error: str | None = None


class CleanupReportResponse(BaseModel):
    containers: list[CleanupOutcomeDto]
    caddy_routes_removed: list[str]
    worktrees_removed: list[str]
    worktree_errors: list[dict[str, str]]
    projects_purged: list[str] = []
    project_purge_errors: list[dict[str, str]] = []


class CleanupRequest(BaseModel):
    """Body for `POST /cleanup/orphans`. The server always recomputes
    the orphan set itself — `remove_worktrees` is the only knob
    available to the client (filesystem deletion is destructive)."""

    remove_worktrees: bool = False


# ─────────────────────────────────────────────── DB enrichment ──


async def _load_db_context(
    db: AsyncSession,
) -> tuple[
    dict[str, models.Project],
    dict[str, models.Workspace],
    dict[str, models.Environment],
    dict[str, "SessionMetricsAccumulator"],
]:
    """Load the projects / workspaces / environments tables + the
    workspace→agent_session metric rollup once per request. The
    samples loop joins against these dicts."""
    from sqlalchemy import func as _sql_func  # noqa: PLC0415

    projects_rows = (await db.execute(select(models.Project))).scalars().all()
    workspaces_rows = (await db.execute(select(models.Workspace))).scalars().all()
    envs_rows = (await db.execute(select(models.Environment))).scalars().all()
    # Phase E.2 — aggregate non-archived agent_sessions per workspace
    # so the perf tab can render "this workspace has cost $X / N
    # tokens" without N+1 queries. The single GROUP BY is cheap; we
    # do it inside the same short-lived session as the rest.
    session_rows = (
        await db.execute(
            select(
                models.AgentSession.workspace_id,
                _sql_func.coalesce(_sql_func.sum(models.AgentSession.cost_usd), 0),
                _sql_func.coalesce(
                    _sql_func.sum(models.AgentSession.input_tokens), 0
                ),
                _sql_func.coalesce(
                    _sql_func.sum(models.AgentSession.output_tokens), 0
                ),
                _sql_func.count(models.AgentSession.id),
            )
            .where(
                models.AgentSession.status
                != enums.AgentSessionStatus.ARCHIVED  # type: ignore[attr-defined]
            )
            .group_by(models.AgentSession.workspace_id)
        )
    ).all()
    session_metrics: dict[str, SessionMetricsAccumulator] = {
        row[0]: SessionMetricsAccumulator(
            cost_usd_total=float(row[1] or 0.0),
            input_tokens_total=int(row[2] or 0),
            output_tokens_total=int(row[3] or 0),
            session_count=int(row[4] or 0),
        )
        for row in session_rows
    }
    return (
        {p.id: p for p in projects_rows},
        {w.id: w for w in workspaces_rows},
        {e.id: e for e in envs_rows},
        session_metrics,
    )


@dataclass(frozen=True)
class SessionMetricsAccumulator:
    """Phase E.2 — Pythonic mirror of `SessionMetricsDto` used while
    holding DB rows. Kept separate from the wire-format DTO so the
    payload builder can decide which containers deserve metrics
    (only those with a real workspace_id) without leaking the DB
    aggregate shape into the response."""

    cost_usd_total: float
    input_tokens_total: int
    output_tokens_total: int
    session_count: int


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


def _sample_dto_with_metrics(
    s: ContainerSample,
    session_metrics: dict[str, "SessionMetricsAccumulator"],
) -> SampleDto:
    """Phase E.2 — variant of `_sample_dto` that attaches session
    metrics when the sample belongs to a workspace with active
    agent sessions. Infra containers (no `workspace_id`) get
    `session_metrics=None`."""
    sm: SessionMetricsDto | None = None
    wid = s.summary.workspace_id
    if wid is not None:
        acc = session_metrics.get(wid)
        if acc is not None:
            sm = SessionMetricsDto(
                cost_usd_total=acc.cost_usd_total,
                input_tokens_total=acc.input_tokens_total,
                output_tokens_total=acc.output_tokens_total,
                session_count=acc.session_count,
            )
    return SampleDto(
        summary=_summary_dto(s.summary),
        limits=_limits_dto(s.limits),
        stats=_stats_dto(s.stats),
        session_metrics=sm,
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
    dict[str, "SessionMetricsAccumulator"],
]:
    """Open a short-lived session JUST for the projects/workspaces/
    environments lookup, release the connection immediately. The
    performance endpoints are polled at 3 s while the actual stats
    sampling takes ~1 s of docker I/O — if we held the request-
    scoped session for the whole duration we'd saturate the pool
    when multiple browser tabs poll concurrently. Two empty tables
    + a sub-50ms read does not deserve a long-lived connection."""
    if container.session_factory is None:
        return ({}, {}, {}, {})
    async with container.session_factory() as db:
        return await _load_db_context(db)


def _snapshot_workspace_services(
    container: AppContainer,
) -> dict[str, list[WorkspaceServiceDto]]:
    """Take a snapshot of every ServiceRegistry entry, keyed by the
    workspace_id (uppercase to match `workspaces` keys). Empty dict
    when the registry is empty — same shape as the no-services case
    so the caller doesn't need to branch."""
    bucket: dict[str, list[WorkspaceServiceDto]] = {}
    # Iterate without taking the registry lock — entries are dicts of
    # dataclass instances; reading the fields we expose is benign even
    # if a concurrent start/stop is racing (worst case is a slightly
    # stale state value, which the next 3-s poll corrects).
    for svc in container.services:
        wid = svc.workspace_id.upper()
        bucket.setdefault(wid, []).append(
            WorkspaceServiceDto(
                label=svc.label,
                cmd=svc.cmd,
                port=svc.port,
                auto_port=svc.auto_port,
                state=svc.state.value,
                bound_url=svc.bound_url,
            )
        )
    return bucket


async def _load_last_deploys(
    container: AppContainer,
) -> dict[str, dict[str, str | None]]:
    """Fetch the most recent DeployRun per environment so the
    dashboard can render a placeholder row for envs whose stack is
    currently down. Solo-hobbyist scale (a few envs × a few hundred
    runs) makes the "fetch all and reduce in Python" approach fine —
    if the table grows large enough to matter, swap for a window
    function. Returns `{env_id: {status, started_at, version,
    bound_url}}`."""
    if container.session_factory is None:
        return {}
    async with container.session_factory() as db:
        rows = (
            (await db.execute(select(models.DeployRun))).scalars().all()
        )
    latest: dict[str, models.DeployRun] = {}
    for r in rows:
        prev = latest.get(r.environment_id)
        if prev is None or r.started_at > prev.started_at:
            latest[r.environment_id] = r
    return {
        env_id: {
            "status": r.status,
            "started_at": (
                (r.finished_at or r.started_at).isoformat()
                if (r.finished_at or r.started_at)
                else None
            ),
            "version": r.version,
            "bound_url": r.bound_url,
        }
        for env_id, r in latest.items()
    }


def _build_workspace_dtos(
    workspaces: dict[str, models.Workspace],
    svc_by_workspace: dict[str, list[WorkspaceServiceDto]],
) -> list[WorkspaceDto]:
    return [
        WorkspaceDto(
            id=w.id,
            project_id=w.project_id,
            branch=w.branch,
            status=w.status.value,
            services=svc_by_workspace.get(w.id, []),
        )
        for w in workspaces.values()
    ]


def _build_env_dtos(
    environments: dict[str, models.Environment],
    last_deploys: dict[str, dict[str, str | None]],
) -> list[EnvironmentDto]:
    return [
        EnvironmentDto(
            id=e.id,
            project_id=e.project_id,
            name=e.name,
            last_deploy_status=(last_deploys.get(e.id, {}).get("status")),
            last_deploy_at=(last_deploys.get(e.id, {}).get("started_at")),
            last_deploy_version=(last_deploys.get(e.id, {}).get("version")),
            last_bound_url=(last_deploys.get(e.id, {}).get("bound_url")),
        )
        for e in environments.values()
    ]


@router.get("/containers", response_model=ContainersResponse)
async def list_containers(
    container: AppContainer = Depends(get_container),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> ContainersResponse:
    sampler = _get_sampler()
    # DB read uses its own short-lived session (released before the
    # slow docker sampling begins / in parallel with it). The
    # request-scoped session dependency is intentionally NOT used.
    # Phase E.2 — add `sample_gpu()` to the parallel fan-out so the
    # poll endpoint matches the SSE payload shape (single fetch
    # gives the UI everything it needs).
    samples, db_ctx, last_deploys, gpu_samples = await asyncio.gather(
        sampler.sample_all(),
        _load_db_context_isolated(container),
        _load_last_deploys(container),
        sample_gpu(),
    )
    projects, workspaces, environments, session_metrics = db_ctx
    # In-memory registry — no IO, take in the same thread.
    svc_by_workspace = _snapshot_workspace_services(container)
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
    from gapt_server.domains.workspace_sandbox.manager import (  # noqa: PLC0415
        format_gpus_flag,
    )

    return ContainersResponse(
        samples=[_sample_dto_with_metrics(s, session_metrics) for s in enriched],
        projects=[
            ProjectDto(
                id=p.id,
                slug=p.slug,
                display_name=p.display_name,
                archived_at=(p.archived_at.isoformat() if p.archived_at else None),
            )
            for p in projects.values()
        ],
        workspaces=_build_workspace_dtos(workspaces, svc_by_workspace),
        environments=_build_env_dtos(environments, last_deploys),
        total_containers=len(enriched),
        running_containers=running,
        total_cpu_pct=total_cpu,
        total_mem_bytes=total_mem,
        gpus=[_gpu_dto(g) for g in gpu_samples],
        applied_gpu_policy=format_gpus_flag(container.settings.workspace_gpus),
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
    projects, workspaces, environments, session_metrics = await _load_db_context_isolated(
        container
    )
    enriched = ContainerSample(
        summary=_enrich_summary(sample.summary, projects, workspaces, environments),
        limits=sample.limits,
        stats=sample.stats,
    )
    return _sample_dto_with_metrics(enriched, session_metrics)


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


@router.get("/stream")
async def stream_containers(
    request: Request,
    container: AppContainer = Depends(get_container),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> EventSourceResponse:
    """SSE feed of the containers payload. Push cadence ~2 s. The
    backing broadcaster only samples while at least one client is
    connected — close the connection (tab hidden, navigate away)
    and the sampling loop self-cancels. Auto-reconnect happens
    client-side via the browser's EventSource semantics."""
    broadcaster = _get_broadcaster(container)

    async def event_iter() -> AsyncIterator[dict]:
        try:
            async for frame in broadcaster.subscribe():
                if await request.is_disconnected():
                    break
                # sse-starlette expects either str (plain `data:`) or
                # a dict with `event` / `data` / `id`. We pre-encoded
                # the SSE frame on the broadcaster side; strip the
                # wrapping so sse-starlette doesn't double-encode.
                yield _parse_sse_frame(frame)
        except asyncio.CancelledError:
            return

    return EventSourceResponse(
        event_iter(),
        ping=15,  # keep-alive comment every 15s so proxies don't kill idle
    )


def _parse_sse_frame(frame: bytes) -> dict:
    """Broadcaster encodes raw SSE bytes; sse-starlette wants its
    own dict format. Cheap re-parse — payload is JSON either way."""
    text = frame.decode("utf-8")
    event = "message"
    data = ""
    for line in text.split("\n"):
        if line.startswith("event: "):
            event = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            data = line[len("data: ") :]
    return {"event": event, "data": data}


def _build_subdomain_manager(settings: Settings) -> SubdomainManager | None:
    """Same shape as the service router's helper. Returns None when
    Caddy admin isn't configured — cleanup then just skips the
    Caddy step."""
    if not settings.caddy_admin_url or not settings.caddy_preview_domain:
        return None
    return SubdomainManager(
        client=CaddyAdminClient(settings.caddy_admin_url),
        preview_domain=settings.caddy_preview_domain,
    )


async def _orphan_db_context(
    container: AppContainer,
) -> tuple[
    dict[str, models.Workspace],
    dict[str, models.Environment],
    set[str],
    dict[str, dict[str, object]],
]:
    """Tables needed for orphan classification + DB purge:

      * workspaces + environments (for `_is_orphan` joins)
      * the set of archived project IDs (orphan-classifier backstop)
      * per-archived-project cascade metadata (display_name +
        workspaces/envs/deploy_runs counts) so the cleanup modal can
        preview "5 DB rows will be deleted with this project"
    """
    if container.session_factory is None:
        return ({}, {}, set(), {})
    async with container.session_factory() as db:
        ws_rows = (await db.execute(select(models.Workspace))).scalars().all()
        env_rows = (await db.execute(select(models.Environment))).scalars().all()
        archived_projects = (
            (
                await db.execute(
                    select(models.Project).where(
                        models.Project.archived_at.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        archived_ids = {p.id for p in archived_projects}
        # Build cascade-counts per archived project. The cascade
        # uses ON DELETE CASCADE, so the modal counts mirror the
        # FK chain (workspaces / envs / deploy_runs only — sandboxes
        # and agent_sessions cascade too but are noise for the UI).
        meta: dict[str, dict[str, object]] = {}
        for p in archived_projects:
            ws_count = sum(1 for w in ws_rows if w.project_id == p.id)
            env_ids = [e.id for e in env_rows if e.project_id == p.id]
            env_count = len(env_ids)
            if env_ids:
                deploy_run_count = (
                    await db.execute(
                        select(models.DeployRun).where(
                            models.DeployRun.environment_id.in_(env_ids)
                        )
                    )
                ).scalars().all()
                dr_count = len(list(deploy_run_count))
            else:
                dr_count = 0
            meta[p.id] = {
                "display_name": p.display_name,
                "workspaces": ws_count,
                "environments": env_count,
                "deploy_runs": dr_count,
            }
    return (
        {w.id: w for w in ws_rows},
        {e.id: e for e in env_rows},
        archived_ids,
        meta,
    )


def _target_dto(t) -> OrphanTargetDto:  # type: ignore[no-untyped-def]
    return OrphanTargetDto(
        container_id=t.container_id,
        container_name=t.container_name,
        category=t.category.value,
        workspace_id=t.workspace_id,
        environment_id=t.environment_id,
        worktree_path=t.worktree_path,
        status=t.status,
    )


def _plan_dto(plan: OrphanPlan) -> OrphanPlanResponse:
    return OrphanPlanResponse(
        containers=[_target_dto(t) for t in plan.containers],
        caddy_route_ids=plan.caddy_route_ids,
        worktree_paths=plan.worktree_paths,
        archived_projects=[
            ArchivedProjectPurgeDto(
                project_id=p.project_id,
                display_name=p.display_name,
                cascade_workspaces=p.cascade_workspaces,
                cascade_environments=p.cascade_environments,
                cascade_deploy_runs=p.cascade_deploy_runs,
            )
            for p in plan.archived_projects
        ],
    )


@router.get("/orphans", response_model=OrphanPlanResponse)
async def preview_orphan_cleanup(
    container: AppContainer = Depends(get_container),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> OrphanPlanResponse:
    """Dry-run preview of what `POST /cleanup/orphans` would
    delete — surfaced in the confirmation modal so the operator
    can see the blast radius before clicking the destructive
    button."""
    sampler = _get_sampler()
    workspaces, environments, archived_projects, archived_meta = (
        await _orphan_db_context(container)
    )
    caddy_manager = _build_subdomain_manager(settings)
    plan = await build_orphan_plan(
        sampler,
        workspaces_by_id=workspaces,
        environments_by_id=environments,
        caddy_manager=caddy_manager,
        archived_project_ids=archived_projects,
        archived_projects_meta=archived_meta,
    )
    return _plan_dto(plan)


@router.post("/cleanup/orphans", response_model=CleanupReportResponse)
async def cleanup_orphans(
    body: CleanupRequest,
    container: AppContainer = Depends(get_container),  # noqa: B008
    settings: Settings = Depends(get_app_settings),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> CleanupReportResponse:
    """Stop + remove every orphan container, drop stale Caddy
    preview routes, optionally `rm -rf` the bind-mounted worktree
    directories. The set of orphans is recomputed server-side from
    the live container list + DB rows — the client cannot trick
    this into deleting a live workspace."""
    sampler = _get_sampler()
    workspaces, environments, archived_projects, archived_meta = (
        await _orphan_db_context(container)
    )
    caddy_manager = _build_subdomain_manager(settings)
    plan = await build_orphan_plan(
        sampler,
        workspaces_by_id=workspaces,
        environments_by_id=environments,
        caddy_manager=caddy_manager,
        archived_project_ids=archived_projects,
        archived_projects_meta=archived_meta,
    )
    report = await execute_cleanup(
        plan,
        sampler=sampler,
        caddy_manager=caddy_manager,
        remove_worktrees=body.remove_worktrees,
        db_session_factory=container.session_factory,
    )
    # Bust the broadcaster's replay cache so the next stream tick
    # reflects the smaller container set without a 2 s stale frame.
    global _BROADCASTER  # noqa: PLW0603
    if _BROADCASTER is not None:
        _BROADCASTER._latest = None  # noqa: SLF001
        # Force a fresh sample + broadcast right now so the SSE
        # clients update within ~100 ms instead of waiting for the
        # next 2-s sampler tick. Without this the UI shows stale
        # "0 실행 중" containers for up to 2 s after a "전부 정리".
        try:
            await _BROADCASTER.force_push()
        except Exception:  # noqa: BLE001
            # Best-effort — broadcaster might not be wired with a
            # payload builder in some test setups.
            pass
    # Also invalidate the sampler TTL cache for the same reason.
    sampler._sample_cache = None  # noqa: SLF001
    return CleanupReportResponse(
        containers=[
            CleanupOutcomeDto(
                container_id=o.container_id,
                container_name=o.container_name,
                ok=o.ok,
                error=o.error,
            )
            for o in report.containers
        ],
        caddy_routes_removed=report.caddy_routes_removed,
        worktrees_removed=report.worktrees_removed,
        worktree_errors=report.worktree_errors,
        projects_purged=report.projects_purged,
        project_purge_errors=report.project_purge_errors,
    )


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
    container: AppContainer = Depends(get_container),  # noqa: B008
    _user: AdminPrincipal = Depends(get_current_user),  # noqa: B008
) -> GpusResponse:
    from gapt_server.domains.workspace_sandbox.manager import (  # noqa: PLC0415
        format_gpus_flag,
    )

    gpus = await sample_gpu()
    # Phase E.1 — `applied_policy` is the normalised arg we'd pass to
    # `docker run --gpus`. Surfaces the operator-set policy back to
    # the UI so the Settings page can say "containers boot with
    # device=0,1" or "containers boot CPU-only".
    applied = format_gpus_flag(container.settings.workspace_gpus)
    return GpusResponse(
        available=bool(gpus),
        gpus=[_gpu_dto(g) for g in gpus],
        applied_policy=applied,
    )
