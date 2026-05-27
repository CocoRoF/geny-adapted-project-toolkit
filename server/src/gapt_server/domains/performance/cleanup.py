"""Orphan-container cleanup.

An *orphan* is a docker container that GAPT created in the past but
no longer has a DB row to back it — typical sources:

  * archived / deleted workspace (`workspaces` row gone), container
    survived because nothing called `docker rm`
  * agent-runtime sandboxes (`gapt-<ULID>`) that always exit ~0 and
    accumulate
  * prod compose stacks whose Environment row was dropped

Operator wants to clean them up in one click. The "safe" set is:

  1. Stop & remove the orphan containers themselves.
  2. Unregister stale Caddy preview routes pointing at those slugs.
  3. *Optionally* `rm -rf` the host-side worktree directory the
     container was bind-mounting (destructive — opt-in via flag).

We never touch a container that resolves to an *existing*
workspace / environment, even if the client passed it. The server
re-classifies every target before acting.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from gapt_server.domains.performance.sampler import (
    ContainerCategory,
    ContainerSample,
    ContainerSampler,
)

if TYPE_CHECKING:
    from gapt_server.db import models
    from gapt_server.domains.caddy.subdomain import SubdomainManager

logger = structlog.get_logger(__name__)


# Worktree path safety guard. We refuse to `rm -rf` anything that
# doesn't sit under one of these prefixes — defense-in-depth against
# accidentally nuking a random host directory if a label has been
# tampered with. The path must also be absolute.
_WORKTREE_ALLOWED_ROOTS: tuple[str, ...] = ("/workspace/",)


@dataclass(frozen=True)
class OrphanTarget:
    """One unit of work the cleanup will operate on."""

    container_id: str
    container_name: str
    category: ContainerCategory
    workspace_id: str | None
    environment_id: str | None
    # Container's bind-mounted host worktree, if any. None when the
    # container has no `/workspace` mount (agent runtime sandboxes
    # are like this).
    worktree_path: str | None
    # Container's current state (`running`, `exited`, …). Drives
    # whether we need to stop first or can go straight to remove.
    status: str


@dataclass(frozen=True)
class OrphanPlan:
    """Pre-computed plan returned to the UI before any destructive
    action runs. The frontend renders this so the operator sees what
    will happen, then confirms."""

    containers: list[OrphanTarget]
    caddy_route_ids: list[str]
    worktree_paths: list[str]


@dataclass(frozen=True)
class CleanupOutcome:
    container_id: str
    container_name: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class CleanupReport:
    containers: list[CleanupOutcome]
    caddy_routes_removed: list[str]
    worktrees_removed: list[str]
    worktree_errors: list[dict[str, str]]


# ─────────────────────────────────────────────────── detection ──


def _container_worktree(attrs: dict) -> str | None:
    """Extract the host-side worktree from a container's mount list.
    Returns the source of the mount targeted at `/workspace` if
    present and shaped like a real worktree."""
    for m in attrs.get("Mounts", []) or attrs.get("HostConfig", {}).get("Mounts", []) or []:
        target = m.get("Destination") or m.get("Target")
        source = m.get("Source") or m.get("source")
        if target == "/workspace" and isinstance(source, str) and source.startswith("/"):
            return source
    return None


def _is_orphan(
    sample: ContainerSample,
    workspaces_by_id: dict[str, "models.Workspace"],
    environments_by_id: dict[str, "models.Environment"],
    archived_project_ids: set[str],
) -> bool:
    """A sample is orphan when its workspace_id / environment_id
    don't resolve to a live DB row, OR when its owning project has
    been archived.

    Edge cases:
      * Archived workspace → row exists but `status == ARCHIVED`
        → treated as orphan (the user already said "stop using this").
      * Archived parent project → the cascade should have flipped
        the workspace status, but if it failed mid-way OR the project
        was archived before the cascade existed, the workspace row
        still says RUNNING. Cross-check the project's archived state
        as a backstop so the dashboard never leaves zombies behind.
      * agent-runtime `gapt-<ULID>` containers (no workspace_id label
        and not workspace category) → orphan unless project_id
        somehow resolves.
    """
    if sample.summary.category == ContainerCategory.WORKSPACE:
        ws_id = sample.summary.workspace_id
        if ws_id is None:
            return True
        row = workspaces_by_id.get(ws_id)
        if row is None:
            return True
        from gapt_server.db import enums  # noqa: PLC0415 — avoid module cycle

        if row.status == enums.WorkspaceStatus.ARCHIVED:
            return True
        # Project-level backstop: the workspace status might still be
        # RUNNING because the cascade failed on this row, but the
        # project itself was archived → no point keeping the container.
        return row.project_id in archived_project_ids
    if sample.summary.category == ContainerCategory.PROD:
        env_id = sample.summary.environment_id
        if env_id is None:
            return True
        env = environments_by_id.get(env_id)
        if env is None:
            return True
        # Same project-level backstop as workspaces — a prod stack
        # whose owning project is archived has no legitimate reason
        # to keep running.
        return env.project_id in archived_project_ids
    # Infra (`gapt-dev-*`) is never orphan — those are control-plane.
    if sample.summary.category == ContainerCategory.INFRA:
        return False
    # Anything else (gapt-* prefix without label) → orphan candidate.
    return True


async def build_plan(
    sampler: ContainerSampler,
    *,
    workspaces_by_id: dict[str, "models.Workspace"],
    environments_by_id: dict[str, "models.Environment"],
    caddy_manager: "SubdomainManager | None",
    archived_project_ids: set[str] | None = None,
) -> OrphanPlan:
    """Snapshot the host + DB state once, return the cleanup target
    list. The same function backs both the dry-run preview and the
    real cleanup — we never trust the client's idea of "this is an
    orphan", we recompute it server-side."""
    samples = await sampler.sample_all()
    archived = archived_project_ids or set()
    orphan_samples = [
        s
        for s in samples
        if _is_orphan(s, workspaces_by_id, environments_by_id, archived)
    ]

    targets: list[OrphanTarget] = []
    worktrees: set[str] = set()
    for s in orphan_samples:
        # Re-fetch attrs from the docker daemon for the worktree
        # mount (the sampler's snapshot does carry it but we go to
        # the source for the destructive plan).
        attrs = await asyncio.to_thread(_attrs_for, sampler, s.summary.id)
        worktree = _container_worktree(attrs) if attrs else None
        if worktree and any(worktree.startswith(p) for p in _WORKTREE_ALLOWED_ROOTS):
            worktrees.add(worktree)
        targets.append(
            OrphanTarget(
                container_id=s.summary.id,
                container_name=s.summary.name,
                category=s.summary.category,
                workspace_id=s.summary.workspace_id,
                environment_id=s.summary.environment_id,
                worktree_path=worktree,
                status=s.summary.status,
            )
        )

    caddy_route_ids: list[str] = []
    if caddy_manager is not None:
        try:
            existing = await caddy_manager.list_routes()
        except Exception:  # noqa: BLE001
            existing = []
        live_workspace_ids_lower = {wid.lower() for wid in workspaces_by_id}
        for route in existing:
            rid = (route.get("@id") or "") if isinstance(route, dict) else ""
            if not rid.startswith("gapt-preview-"):
                continue
            # `@id` looks like `gapt-preview-<workspace_id>-<label>`
            # (or `…-asset` for the Referer-fallback variant). Pluck
            # the workspace_id substring and check if the underlying
            # workspace still exists.
            without_prefix = rid[len("gapt-preview-") :]
            slug = without_prefix.split("-", 1)[0]
            if slug not in live_workspace_ids_lower:
                caddy_route_ids.append(rid)

    return OrphanPlan(
        containers=targets,
        caddy_route_ids=caddy_route_ids,
        worktree_paths=sorted(worktrees),
    )


def _attrs_for(sampler: ContainerSampler, container_id: str) -> dict | None:
    try:
        return sampler._client.containers.get(container_id).attrs  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None


# ──────────────────────────────────────────────────── execute ──


async def execute_cleanup(
    plan: OrphanPlan,
    *,
    sampler: ContainerSampler,
    caddy_manager: "SubdomainManager | None",
    remove_worktrees: bool,
) -> CleanupReport:
    """Carry out the plan. Stops + removes each container, unregisters
    each Caddy route, optionally removes worktree directories.

    All operations are best-effort and per-target — one failed
    removal doesn't abort the rest. The report captures every error
    so the UI can show what didn't work."""
    # 1. Containers — stop (graceful) then force-remove. We do them
    # in parallel; each one's docker calls are independent.
    container_results = await asyncio.gather(
        *(_remove_one(sampler, t) for t in plan.containers),
        return_exceptions=False,
    )

    # 2. Caddy preview routes — single-flight DELETE per id.
    removed_routes: list[str] = []
    if caddy_manager is not None:
        for rid in plan.caddy_route_ids:
            try:
                await caddy_manager.client.delete(f"/id/{rid}")
                removed_routes.append(rid)
            except Exception:  # noqa: BLE001
                # 404 is fine — route already gone. Anything else
                # we swallow + continue; nothing depends on this
                # succeeding for the user's flow.
                continue

    # 3. Worktree directories (opt-in, destructive). Server-side
    # safety guards: must be under /workspace/*, must not be a
    # symlink, must not contain the host's path traversal markers.
    removed_worktrees: list[str] = []
    worktree_errors: list[dict[str, str]] = []
    if remove_worktrees:
        for path in plan.worktree_paths:
            err = await asyncio.to_thread(_rm_worktree, path)
            if err is None:
                removed_worktrees.append(path)
            else:
                worktree_errors.append({"path": path, "reason": err})

    return CleanupReport(
        containers=container_results,
        caddy_routes_removed=removed_routes,
        worktrees_removed=removed_worktrees,
        worktree_errors=worktree_errors,
    )


async def _remove_one(sampler: ContainerSampler, target: OrphanTarget) -> CleanupOutcome:
    try:
        if target.status == "running":
            try:
                await sampler.stop(target.container_id, timeout_s=5)
            except Exception:  # noqa: BLE001
                # Couldn't stop cleanly — proceed to force remove.
                pass

        def _do_remove() -> None:
            c = sampler._client.containers.get(target.container_id)  # noqa: SLF001
            c.remove(force=True)

        await asyncio.to_thread(_do_remove)
        return CleanupOutcome(
            container_id=target.container_id,
            container_name=target.container_name,
            ok=True,
        )
    except Exception as exc:  # noqa: BLE001
        return CleanupOutcome(
            container_id=target.container_id,
            container_name=target.container_name,
            ok=False,
            error=str(exc),
        )


def _rm_worktree(path: str) -> str | None:
    """Best-effort `rm -rf`. Returns None on success, an error
    string on refusal/failure."""
    import os  # noqa: PLC0415

    if not path.startswith("/") or any(path.startswith(p) is False for p in _WORKTREE_ALLOWED_ROOTS):
        # Re-check the guard — `build_plan` already filtered but
        # the caller might pass arbitrary paths.
        if not any(path.startswith(p) for p in _WORKTREE_ALLOWED_ROOTS):
            return f"path {path!r} not under any allowed worktree root"
    if os.path.islink(path):
        return f"refusing to delete symlink at {path!r}"
    if not os.path.isdir(path):
        # If it's already gone, treat as success.
        if not os.path.exists(path):
            return None
        return f"path {path!r} is not a directory"
    try:
        shutil.rmtree(path)
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
