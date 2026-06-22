"""Host capability probe — the runtime dependencies the GAPT server
needs to actually run workspace sandboxes.

Creating a workspace spawns a per-workspace container under the sysbox
runtime and `docker exec`s the Claude Code CLI inside it. If any link
in that chain is missing the create used to fail opaquely at click
time. This module probes the chain up front so the API + UI can warn
the operator with an actionable remedy instead.

Checked from the server's own vantage point (it's the orchestrator):
  - docker CLI on PATH        — the sandbox runner shells out to it
  - docker daemon reachable   — the SDK client used for create/inspect
  - sandbox runtime present    — `sysbox-runc` registered with the daemon
  - workspace image built      — `gapt-workspace:latest` (bundles Node,
                                 Python, and the Claude Code CLI)

Pure-ish: the docker calls are isolated in `_probe_sync` (run off the
event loop) so the report shape unit-tests with a stub.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from dataclasses import dataclass

# Capability keys that must all be "ok" for workspace creation to work.
REQUIRED_KEYS = ("docker_cli", "docker_daemon", "sandbox_runtime", "workspace_image")


@dataclass
class Capability:
    """One probed dependency. `state` drives the UI colour band:
    "ok" (green), "missing" (the dependency isn't there — warn), or
    "degraded" (couldn't determine, usually because an upstream
    dependency like the daemon is already down)."""

    key: str
    label: str
    state: str  # "ok" | "missing" | "degraded"
    detail: str
    remedy: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "detail": self.detail,
            "remedy": self.remedy,
        }


@dataclass
class CapabilityReport:
    capabilities: list[Capability]
    # True only when every REQUIRED_KEYS capability is "ok" — the gate
    # the UI uses to decide whether to warn that workspaces are down.
    workspaces_ready: bool

    def to_json(self) -> dict[str, object]:
        return {
            "workspaces_ready": self.workspaces_ready,
            "capabilities": [c.to_json() for c in self.capabilities],
        }

    @property
    def missing(self) -> list[Capability]:
        return [c for c in self.capabilities if c.state != "ok"]


def _probe_sync(runtime: str, image: str) -> list[Capability]:
    """The blocking half — `shutil.which` + docker SDK calls. Each
    failure degrades to a Capability row rather than raising, so one
    missing link never hides the others."""
    caps: list[Capability] = []

    docker_bin = shutil.which("docker")
    caps.append(
        Capability(
            key="docker_cli",
            label="Docker CLI",
            state="ok" if docker_bin else "missing",
            detail=(f"`docker` found at {docker_bin}" if docker_bin else "`docker` is not on PATH"),
            remedy=(
                None
                if docker_bin
                else "Add the Docker CLI to the server image "
                "(server/Dockerfile copies it from docker:cli)."
            ),
        )
    )

    info: dict | None = None
    client = None
    try:
        from gapt_server.domains.sandbox.sysbox_backend import (  # noqa: PLC0415
            make_default_client,
        )

        client = make_default_client()
        info = client.info()
        caps.append(
            Capability(
                key="docker_daemon",
                label="Docker daemon",
                state="ok",
                detail=f"reachable — Docker {info.get('ServerVersion', '?')}",
            )
        )
    except Exception as exc:
        caps.append(
            Capability(
                key="docker_daemon",
                label="Docker daemon",
                state="missing",
                detail=f"unreachable: {exc}",
                remedy="Mount /var/run/docker.sock into the server and set "
                "GAPT_DOCKER_GID to the host docker group.",
            )
        )

    if info is not None:
        runtimes = info.get("Runtimes") or {}
        rt_ok = runtime in runtimes
        others = ", ".join(sorted(runtimes)) or "none"
        caps.append(
            Capability(
                key="sandbox_runtime",
                label=f"Sandbox runtime ({runtime})",
                state="ok" if rt_ok else "missing",
                detail=(
                    f"`{runtime}` is registered with the daemon"
                    if rt_ok
                    else f"`{runtime}` not among the daemon's runtimes ({others})"
                ),
                remedy=(
                    None
                    if rt_ok
                    else "Install sysbox-ce on the host — it registers the "
                    "sysbox-runc runtime and restarts dockerd."
                ),
            )
        )
    else:
        caps.append(
            Capability(
                key="sandbox_runtime",
                label=f"Sandbox runtime ({runtime})",
                state="degraded",
                detail="cannot check — docker daemon unreachable",
                remedy="Restore docker access first.",
            )
        )

    if client is not None:
        try:
            client.images.get(image)
            img_ok, img_detail = True, f"`{image}` is built"
        except Exception:
            img_ok, img_detail = False, f"`{image}` is not built"
        caps.append(
            Capability(
                key="workspace_image",
                label="Workspace image (Node + Python + Claude Code CLI)",
                state="ok" if img_ok else "missing",
                detail=img_detail,
                remedy=(
                    None
                    if img_ok
                    else "Build it on the host: `docker/workspace/build.sh` "
                    "(bundles the Claude Code CLI)."
                ),
            )
        )
    else:
        caps.append(
            Capability(
                key="workspace_image",
                label="Workspace image (Node + Python + Claude Code CLI)",
                state="degraded",
                detail="cannot check — docker daemon unreachable",
            )
        )

    if client is not None:
        with contextlib.suppress(Exception):
            client.close()

    return caps


async def probe_capabilities(*, runtime: str, image: str) -> CapabilityReport:
    """Probe the workspace-sandbox dependency chain. Never raises — a
    broken link becomes a Capability row. Docker calls run off the
    event loop."""
    caps = await asyncio.to_thread(_probe_sync, runtime, image)
    by_key = {c.key: c for c in caps}
    workspaces_ready = all(
        (by_key.get(k) is not None and by_key[k].state == "ok") for k in REQUIRED_KEYS
    )
    return CapabilityReport(capabilities=caps, workspaces_ready=workspaces_ready)
