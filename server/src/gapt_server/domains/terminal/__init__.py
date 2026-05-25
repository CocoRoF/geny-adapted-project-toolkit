"""Workspace terminal — PTY-backed interactive shell + service log tail.

`PtyHandle` wraps a fork+exec'd subprocess attached to a pseudo-tty so
output formatting (colors / progress bars / curses redraws) round-trips
correctly. The router layer (`routers/terminal.py`) bridges a single
WebSocket per session: client → input/resize JSON frames, server →
output bytes.

For service-logs (long-running background processes started elsewhere —
e.g. `npm run dev` from a prior terminal session, or compose services),
`LogTail` mirrors a file or `tail -F` style follower. It returns an
async iterator of new chunks plus a `close()` for the SSE handler.

Dev (Mock sandbox) runs PTY on the host worktree directly. Sysbox prod
swaps the implementation for `docker exec -it <container> ...` via the
backend protocol — same `PtyHandle` shape from the caller's view.
"""

from gapt_server.domains.terminal.pty import (
    PtyClosed,
    PtyHandle,
    PtySpawnError,
    spawn_pty,
)

__all__ = [
    "PtyClosed",
    "PtyHandle",
    "PtySpawnError",
    "spawn_pty",
]
