"""Per-workspace Docker sandbox.

Every workspace gets ONE long-lived container (`gapt-ws-<wid>`) that
bind-mounts its worktree at `/workspace`. Terminal PTYs, dev-server
processes, *and* agent tool calls all funnel through `docker exec`
into this container so the user / agent can never see anything past
the worktree — no `/home`, no sibling workspaces, no host config.

The container is started lazily on first use and torn down on
workspace delete (or on container `aclose`). The image is configurable
(`GAPT_WORKSPACE_SANDBOX_IMAGE`, default `ubuntu:24.04`); users with
a project-specific Dockerfile can `docker build -t my-project:dev .`
and point the env var at it.

Failure mode: if Docker isn't installed / reachable, every entry point
raises `WorkspaceSandboxUnavailable` with a clear `code` so the router
layer can surface it as a 412/503 instead of crashing.
"""

from gapt_server.domains.workspace_sandbox.manager import (
    WorkspaceSandbox,
    WorkspaceSandboxError,
    WorkspaceSandboxManager,
    WorkspaceSandboxUnavailable,
)

__all__ = [
    "WorkspaceSandbox",
    "WorkspaceSandboxError",
    "WorkspaceSandboxManager",
    "WorkspaceSandboxUnavailable",
]
