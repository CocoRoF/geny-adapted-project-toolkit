"""In-process uvicorn fixture for tests that need real socket streaming.

Phase M.3 — `tests/sessions/test_routes.py::test_stream_emits_text_and_done`
was skipped because httpx's `ASGITransport` buffers chunks until the
generator returns, hiding the SSE intermediate frames the route emits.
The keep-alive + multi-turn contract was unit-tested at the
`stream_to_async_iter` level but the route layer (`/_gapt/api/sessions/
{id}/stream` + `StreamingResponse` + middleware stack) had no
end-to-end coverage.

This helper spins up a real uvicorn server bound to an ephemeral port
so tests can connect via `httpx.AsyncClient(base_url=...)` and observe
streaming frames as they arrive. The server runs in the same event
loop as the test (no thread-jumping) which keeps assertion semantics
unsurprising and lets the test share the same asyncio context as the
container's background tasks (session sweep, audit flusher, etc.).
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI


def _free_tcp_port() -> int:
    """Reserve an unused TCP port. The socket closes before uvicorn
    binds, so there is a brief race window — fine for local dev/CI
    where we're the only consumer."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class RunningServer:
    base_url: str
    host: str
    port: int


@contextlib.asynccontextmanager
async def run_uvicorn(app: FastAPI) -> AsyncIterator[RunningServer]:
    """Start ``app`` on an ephemeral port for the duration of the
    `async with` block. Yields the base URL the test should hit.

    Uses uvicorn's `Server.serve()` driven as an asyncio task so the
    server shares the test's event loop. We wait on `started` via a
    polling loop because `Server.startup()` doesn't expose a clean
    "ready" awaitable.
    """
    port = _free_tcp_port()
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        # Lifespan must run so the container's `start_sweep()` etc. fire,
        # matching how the prod server boots.
        lifespan="on",
        # Skip the websockets impl autoload — uvicorn 0.35 + websockets
        # ≥14 emits a DeprecationWarning at import that pytest's strict
        # warning filter promotes to test failure. We don't speak
        # websockets in any of these tests; SSE is plain HTTP chunking.
        ws="none",
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve(), name="test-uvicorn")
    # Poll the started flag — uvicorn flips it once the socket is
    # listening and the lifespan handler's `yield` is reached.
    for _ in range(200):  # ~2s budget
        if server.started:
            break
        await asyncio.sleep(0.01)
    else:  # pragma: no cover — only fires on a broken boot
        server.should_exit = True
        await serve_task
        raise RuntimeError("uvicorn fixture failed to start within 2s")
    try:
        yield RunningServer(
            base_url=f"http://127.0.0.1:{port}",
            host="127.0.0.1",
            port=port,
        )
    finally:
        server.should_exit = True
        # `serve()` returns once the shutdown handler finishes; we
        # bound it so a hung handler doesn't wedge the test session.
        try:
            await asyncio.wait_for(serve_task, timeout=5.0)
        except TimeoutError:  # pragma: no cover
            serve_task.cancel()
            with contextlib.suppress(BaseException):
                await serve_task
