"""Container resource observability — D11.

GAPT runs three classes of docker containers per host:

  1. **Workspaces** (`gapt-ws-<wid>`) — the per-workspace dev sandbox.
     Identified by the `gapt.workspace_id=<wid>` label.
  2. **Prod compose stacks** (`gapt-prod-<env_id>-*`) — `docker compose`
     stacks the deploy orchestrator booted for an Environment.
     Identified by the `com.docker.compose.project=gapt-prod-<env_id>`
     label.
  3. **Infra** — the GAPT control plane itself (`gapt-dev-*`) plus
     anything else on the host. Identified by best-effort name
     prefix matching.

The performance API surfaces a unified view: live CPU / memory / I/O
per container + the resource *limits* set at create time (so the
operator can see "is this hitting its cgroup ceiling?"). Sampling
goes through the docker SDK, which is synchronous, so we wrap calls
in `asyncio.to_thread` and parallelise across containers.
"""

from gapt_server.domains.performance.gpu import GpuSample, sample as sample_gpu
from gapt_server.domains.performance.sampler import (
    ContainerCategory,
    ContainerLimits,
    ContainerSample,
    ContainerSampler,
    ContainerStats,
    ContainerSummary,
)

__all__ = [
    "ContainerCategory",
    "ContainerLimits",
    "ContainerSample",
    "ContainerSampler",
    "ContainerStats",
    "ContainerSummary",
    "GpuSample",
    "sample_gpu",
]
