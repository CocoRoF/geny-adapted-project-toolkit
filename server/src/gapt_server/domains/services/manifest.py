"""Durable desired-state for dev services.

The service registry is in-process: its table of running dev servers
lives only as long as the GAPT server. The *processes* live inside the
long-lived `gapt-ws-<wid>` container and survive a GAPT restart, but
the registry's knowledge of them did not — recovery re-scanned the
container for live `GAPT_SVC=` markers and rebuilt a thin entry, losing
the declared port, env, and expose binding, and missing any process
that had shed its marker (Turbopack's detached `next-server` is the
classic case: the marker-carrying wrapper dies, the server keeps the
port).

A manifest fixes that. Every service writes its full definition +
desired state to `<worktree>/.gapt/services/<label>.svc.json`. The
worktree is bind-mounted, so the file outlives the GAPT process and
sits beside the service's `.log`. On boot the reconciler reads the
manifests as the source of truth and converges the container to them:
adopt a still-running service with its full metadata, or restart one
whose process died while it was meant to be running.

Kept as a standalone module of pure filesystem helpers so the registry
stays DB-agnostic (it never grew a session dependency) and so the
read/write round-trip unit-tests without a sandbox.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Manifests live alongside the per-service logs the registry already
# writes. `.svc.json` (not `.json`) so a glob can't confuse them with
# anything else a tool might drop in the directory.
_SERVICES_DIRNAME = (".gapt", "services")
_MANIFEST_SUFFIX = ".svc.json"

# Desired-state values. "running" → the operator wants this service up
# (reconcile restarts it if its process died); "stopped" → the operator
# deliberately stopped it (reconcile surfaces it but never auto-starts).
DESIRED_RUNNING = "running"
DESIRED_STOPPED = "stopped"


@dataclass
class ServiceManifest:
    """The durable definition of one dev service. Mirrors the subset of
    `Service` needed to fully reconstruct or restart it after a GAPT
    restart — everything the in-memory entry would otherwise lose."""

    label: str
    cmd: str
    desired_state: str = DESIRED_RUNNING
    # The port the USER declared at start (None = auto-detect). Restart
    # must replay the user's intent, not a previous run's log detection.
    user_port: int | None = None
    env: dict[str, str] = field(default_factory=dict)
    # Expose binding so the preview survives a restart: None = not
    # exposed; otherwise the Caddy host + url the service was bound to.
    expose: dict[str, str] | None = None
    updated_at: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, object]:
        return {
            "label": self.label,
            "cmd": self.cmd,
            "desired_state": self.desired_state,
            "user_port": self.user_port,
            "env": self.env,
            "expose": self.expose,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json(cls, raw: dict[str, object]) -> ServiceManifest | None:
        """Parse a manifest dict, tolerating older/partial shapes.
        Returns None when the two load-bearing fields (label, cmd) are
        missing or the wrong type — a corrupt file must not wedge boot
        recovery for every other service."""
        label = raw.get("label")
        cmd = raw.get("cmd")
        if not isinstance(label, str) or not isinstance(cmd, str):
            return None
        desired = raw.get("desired_state")
        if desired not in (DESIRED_RUNNING, DESIRED_STOPPED):
            desired = DESIRED_RUNNING
        port = raw.get("user_port")
        user_port = port if isinstance(port, int) else None
        env_raw = raw.get("env")
        env = {str(k): str(v) for k, v in env_raw.items()} if isinstance(env_raw, dict) else {}
        expose_raw = raw.get("expose")
        expose = (
            {str(k): str(v) for k, v in expose_raw.items()}
            if isinstance(expose_raw, dict)
            else None
        )
        ts = raw.get("updated_at")
        updated_at = float(ts) if isinstance(ts, (int, float)) else time.time()
        return cls(
            label=label,
            cmd=cmd,
            desired_state=desired,
            user_port=user_port,
            env=env,
            expose=expose,
            updated_at=updated_at,
        )


def _services_dir(worktree_path: str) -> Path:
    return Path(worktree_path, *_SERVICES_DIRNAME)


def manifest_path(worktree_path: str, label: str) -> Path:
    return _services_dir(worktree_path) / f"{label}{_MANIFEST_SUFFIX}"


def write_manifest(worktree_path: str, manifest: ServiceManifest) -> None:
    """Persist (atomically) a service manifest. Best-effort: a write
    failure is logged but never raised — losing durability must not
    fail the start/stop the caller is performing."""
    manifest.updated_at = time.time()
    path = manifest_path(worktree_path, manifest.label)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(manifest.to_json(), indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic on the same filesystem
    except OSError as exc:
        logger.warning("service.manifest.write_failed", label=manifest.label, error=str(exc))


def update_manifest(
    worktree_path: str,
    label: str,
    *,
    desired_state: str | None = None,
    expose: dict[str, str] | None = None,
    clear_expose: bool = False,
) -> None:
    """Read-modify-write a single field on an existing manifest. No-op
    if the manifest is absent (the service predates this feature, or was
    already removed) — the next full write recreates it."""
    existing = read_manifest(worktree_path, label)
    if existing is None:
        return
    if desired_state is not None:
        existing.desired_state = desired_state
    if clear_expose:
        existing.expose = None
    elif expose is not None:
        existing.expose = expose
    write_manifest(worktree_path, existing)


def read_manifest(worktree_path: str, label: str) -> ServiceManifest | None:
    path = manifest_path(worktree_path, label)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return ServiceManifest.from_json(raw)


def delete_manifest(worktree_path: str, label: str) -> None:
    path = manifest_path(worktree_path, label)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("service.manifest.delete_failed", label=label, error=str(exc))


def list_manifests(worktree_path: str) -> list[ServiceManifest]:
    """All valid manifests in a worktree. Corrupt/unparseable files are
    skipped (logged) rather than aborting the whole scan."""
    out: list[ServiceManifest] = []
    services_dir = _services_dir(worktree_path)
    try:
        names = sorted(p for p in services_dir.glob(f"*{_MANIFEST_SUFFIX}"))
    except OSError:
        return out
    for path in names:
        label = path.name[: -len(_MANIFEST_SUFFIX)]
        manifest = read_manifest(worktree_path, label)
        if manifest is None:
            logger.warning("service.manifest.unreadable", path=str(path))
            continue
        out.append(manifest)
    return out
