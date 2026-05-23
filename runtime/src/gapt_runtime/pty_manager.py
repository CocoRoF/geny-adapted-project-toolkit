"""PTY allocation + lifecycle.

A `PtyManager` owns the open ptys for the daemon process. Each pty:

- is created via `pty.openpty()` on demand,
- spawns a shell with `master_fd` wired to its stdin/stdout/stderr,
- exposes async read / write helpers + a `resize(rows, cols)` ioctl,
- gets garbage-collected when the shell exits or the manager is closed.

WebSocket bridging lives in `handlers_pty.py`; this module stays
transport-agnostic so it can be reused later by a CLI-side helper.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import pty
import signal
import struct
import termios
from dataclasses import dataclass, field

import structlog
from ulid import ULID


def new_ulid() -> str:
    return str(ULID())


logger = structlog.get_logger(__name__)


@dataclass
class PtySession:
    id: str
    master_fd: int
    pid: int
    shell: str
    cwd: str
    cols: int = 80
    rows: int = 24
    closed: bool = field(default=False)


class PtyManager:
    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._lock = asyncio.Lock()

    def list_sessions(self) -> list[PtySession]:
        return [s for s in self._sessions.values() if not s.closed]

    def get(self, session_id: str) -> PtySession | None:
        return self._sessions.get(session_id)

    async def open(
        self,
        *,
        shell: str = "/bin/bash",
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
    ) -> PtySession:
        master_fd, slave_fd = pty.openpty()
        pid = os.fork()
        if pid == 0:  # pragma: no cover — child process branch
            try:
                os.setsid()
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                os.close(master_fd)
                os.close(slave_fd)
                os.chdir(cwd)
                spawn_env = dict(os.environ)
                if env is not None:
                    spawn_env.update(env)
                spawn_env.setdefault("TERM", "xterm-256color")
                os.execvpe(shell, [shell], spawn_env)
            except Exception:
                os._exit(127)
        # Parent
        os.close(slave_fd)
        _set_winsize(master_fd, rows, cols)
        # Non-blocking reads on master_fd so the read loop can hand
        # control back to the event loop between chunks.
        os.set_blocking(master_fd, False)

        session_id = new_ulid()
        session = PtySession(
            id=session_id,
            master_fd=master_fd,
            pid=pid,
            shell=shell,
            cwd=cwd,
            cols=cols,
            rows=rows,
        )
        async with self._lock:
            self._sessions[session_id] = session
        logger.info("pty.opened", session_id=session_id, pid=pid, shell=shell)
        return session

    async def write(self, session_id: str, data: bytes) -> None:
        session = self._require(session_id)
        await asyncio.get_running_loop().run_in_executor(None, _write_all, session.master_fd, data)

    async def read(self, session_id: str, max_bytes: int = 4096) -> bytes:
        session = self._require(session_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bytes] = loop.create_future()

        def _on_readable() -> None:
            if fut.done():
                return
            try:
                chunk = os.read(session.master_fd, max_bytes)
            except BlockingIOError:
                return
            except OSError as exc:
                fut.set_exception(exc)
                _maybe_remove_reader(loop, session.master_fd)
                return
            fut.set_result(chunk or b"")
            _maybe_remove_reader(loop, session.master_fd)

        loop.add_reader(session.master_fd, _on_readable)
        return await fut

    async def resize(self, session_id: str, *, rows: int, cols: int) -> None:
        session = self._require(session_id)
        session.rows = rows
        session.cols = cols
        _set_winsize(session.master_fd, rows, cols)

    async def close(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.closed:
                return
            session.closed = True
            with contextlib.suppress(ProcessLookupError):
                os.kill(session.pid, signal.SIGHUP)
            with contextlib.suppress(OSError):
                os.close(session.master_fd)
        # Reap zombie outside the lock.
        with contextlib.suppress(ChildProcessError):
            await asyncio.get_running_loop().run_in_executor(
                None, os.waitpid, session.pid, os.WNOHANG
            )
        logger.info("pty.closed", session_id=session_id)

    async def aclose(self) -> None:
        for sid in list(self._sessions):
            await self.close(sid)

    def _require(self, session_id: str) -> PtySession:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            raise PtySessionNotFound(session_id)
        return session


class PtySessionNotFound(LookupError):
    """Raised when the requested PTY id doesn't exist or has closed."""


# ─────────────────────────────────────────── low-level helpers ──


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _maybe_remove_reader(loop: asyncio.AbstractEventLoop, fd: int) -> None:
    with contextlib.suppress(OSError, ValueError):
        loop.remove_reader(fd)
