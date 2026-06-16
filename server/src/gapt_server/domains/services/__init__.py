"""Managed background services per workspace.

A `Service` is a single long-running process the user (or the agent)
spawned inside the worktree — typically a dev server (`npm run dev`,
`python -m http.server`, `bun run start`, …). Each service has a
human label, a command, an optional declared port, captured logs in
`{worktree}/.gapt/services/{label}.log`, and (after `expose`) a
Caddy-managed preview URL.

Lifecycle is in-process: the registry lives as long as the server
process. App shutdown SIGTERMs every running service. Compose-style
multi-process workloads are still supported — the user just sets
`cmd="docker compose up"` and we treat it like any other long
runner.

Auto-port-detection scans the first few KB of stdout for common
"server started on :PORT" patterns so the UI can prompt "Expose
:3000?" without the user having to remember the right port. Falls
back to whatever the user typed at start-time.
"""

from gapt_server.domains.services.registry import (
    Service,
    ServiceAlreadyExists,
    ServiceNotFound,
    ServicePortConflict,
    ServiceRegistry,
    ServiceState,
)

__all__ = [
    "Service",
    "ServiceAlreadyExists",
    "ServiceNotFound",
    "ServicePortConflict",
    "ServiceRegistry",
    "ServiceState",
]
