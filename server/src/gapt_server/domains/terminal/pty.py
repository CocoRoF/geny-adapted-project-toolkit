"""PTY-backed subprocess wrapper for the workspace terminal.

Wraps `pty.openpty()` + `asyncio.create_subprocess_exec` so an
interactive shell (or any process that wants a real terminal) can be
driven via async read/write. The router layer feeds these handles
into a WebSocket so the browser xterm.js gets the same byte stream
the process would write to a real tty.

Design notes:

- We allocate a PTY pair, hand the slave end to the child as
  stdin/stdout/stderr, then close it in the parent. Reads/writes go
  through the master end (`master_fd`).
- The reader runs as a single asyncio task per handle, parking on
  `loop.add_reader(...)` so we never block the event loop. Output
  chunks are pushed onto an `asyncio.Queue` the consumer reads via
  `read_chunk()` / `aiter_output()`.
- TTY window-size resize uses the standard `TIOCSWINSZ` ioctl
  + sends `SIGWINCH` to the child so apps like vim / less / npm
  redraw correctly.
- `close()` is idempotent: stops the reader, kills the child
  (process group, so backgrounded jobs die too), drains the queue,
  and closes the master_fd.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios
from dataclasses import dataclass, field
from typing import AsyncIterator


class PtyClosed(Exception):
    """Raised by `read_chunk()` when the PTY is no longer producing
    output (process exited or `close()` called). A sentinel rather
    than a true error — the WebSocket handler treats it as EOF."""


class PtySpawnError(RuntimeError):
    """Spawn failed (binary missing, cwd doesn't exist, permission
    denied). The original errno is wrapped in `__cause__`."""


# Reads from the master fd are sized for one screenful of output —
# bigger buffers waste memory on a quiet terminal, smaller buffers
# add round-trips on a noisy one (npm install with lockfile diffs).
_READ_BUFFER_BYTES = 8192


@dataclass
class PtyHandle:
    """Live PTY + child process. Don't instantiate directly — use
    `spawn_pty()`."""

    master_fd: int
    proc: asyncio.subprocess.Process
    rows: int = 24
    cols: int = 80
    _queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    _closed: bool = False
    _reader_task: asyncio.Task[None] | None = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid

    @property
    def closed(self) -> bool:
        return self._closed

    async def write(self, data: bytes) -> None:
        """Send `data` to the child's stdin. Raises `PtyClosed` after
        `close()`."""
        if self._closed:
            raise PtyClosed("pty is closed")
        # `os.write` is non-blocking on a PTY master in practice — the
        # kernel buffers up to TTYDEF_BUFFER. We loop in case of a
        # short write under back-pressure.
        loop = asyncio.get_running_loop()
        remaining = data
        while remaining:
            written = await loop.run_in_executor(
                None, _try_write, self.master_fd, remaining
            )
            if written <= 0:
                break
            remaining = remaining[written:]

    async def read_chunk(self, timeout: float | None = None) -> bytes:
        """Block until the next output chunk arrives. Raises
        `PtyClosed` on EOF / shutdown. `timeout=None` waits forever."""
        if self._closed and self._queue.empty():
            raise PtyClosed("pty is closed")
        try:
            chunk = (
                await asyncio.wait_for(self._queue.get(), timeout)
                if timeout is not None
                else await self._queue.get()
            )
        except TimeoutError:
            return b""
        if chunk is None:
            raise PtyClosed("pty drained")
        return chunk

    async def aiter_output(self) -> AsyncIterator[bytes]:
        """Async iterator of output chunks until EOF. Stops cleanly
        when `close()` fires."""
        while True:
            try:
                yield await self.read_chunk()
            except PtyClosed:
                return

    def resize(self, rows: int, cols: int) -> None:
        """Re-set the PTY window size. Apps watch SIGWINCH to redraw."""
        if self._closed:
            return
        # `winsize` struct: rows, cols, xpixel, ypixel.
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, packed)
            if self.proc.pid is not None:
                with _suppress_oserror():
                    os.killpg(self.proc.pid, signal.SIGWINCH)
        except OSError:
            # Resize is best-effort; an app that doesn't watch the
            # signal still works fine.
            return
        self.rows, self.cols = rows, cols

    async def wait_exit(self) -> int:
        """Wait for the child to exit and return its exit code.
        Doesn't close the PTY — `close()` does that explicitly."""
        return await self.proc.wait()

    async def close(self) -> None:
        """Idempotent shutdown — stop reader, kill child, drain queue,
        release fd."""
        if self._closed:
            return
        self._closed = True

        if self._reader_task is not None:
            loop = asyncio.get_running_loop()
            with _suppress_oserror():
                loop.remove_reader(self.master_fd)
            self._reader_task.cancel()
            with _suppress_oserror():
                await self._reader_task

        # SIGTERM whole process group so background jobs die too. Give
        # the child a second; SIGKILL otherwise.
        if self.proc.returncode is None:
            with _suppress_oserror():
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=1.0)
            except TimeoutError:
                with _suppress_oserror():
                    os.killpg(self.proc.pid, signal.SIGKILL)
                with _suppress_oserror():
                    await self.proc.wait()

        # Sentinel for any waiter blocked on read_chunk().
        self._queue.put_nowait(None)

        with _suppress_oserror():
            os.close(self.master_fd)


async def spawn_pty(
    *,
    cmd: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    rows: int = 24,
    cols: int = 80,
) -> PtyHandle:
    """Fork + exec `cmd` attached to a fresh PTY. Returns a handle the
    caller writes to / reads from / closes.

    `cwd` defaults to the current working directory. `env` defaults
    to the parent process env merged with TTY-friendly defaults
    (`TERM=xterm-256color`, `COLUMNS`/`LINES`)."""
    if not cmd:
        raise PtySpawnError("empty cmd")

    master_fd, slave_fd = pty.openpty()
    _set_winsize(master_fd, rows, cols)

    # PTY child needs to be its own session leader so SIGWINCH/SIGTERM
    # propagate to backgrounded jobs (`npm run dev` spawns workers).
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    child_env.setdefault("TERM", "xterm-256color")
    child_env["COLUMNS"] = str(cols)
    child_env["LINES"] = str(rows)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=child_env,
            start_new_session=True,
            close_fds=True,
        )
    except (FileNotFoundError, PermissionError) as exc:
        os.close(master_fd)
        os.close(slave_fd)
        raise PtySpawnError(f"spawn failed: {exc}") from exc

    # Parent doesn't need the slave end — closing now (after subprocess
    # spawn) is critical so EOF on master is propagated when the child
    # exits.
    os.close(slave_fd)

    handle = PtyHandle(master_fd=master_fd, proc=proc, rows=rows, cols=cols)
    _start_reader(handle)
    return handle


def _start_reader(handle: PtyHandle) -> None:
    """Wire the master fd into the asyncio loop so reads become
    push-based. We use `loop.add_reader` (not a thread) so the GIL
    stays out of the hot path."""
    loop = asyncio.get_running_loop()

    def _on_readable() -> None:
        if handle._closed:
            return
        try:
            chunk = os.read(handle.master_fd, _READ_BUFFER_BYTES)
        except OSError:
            chunk = b""
        if not chunk:
            # EOF — child closed its end of the PTY. Stop reading and
            # signal consumers.
            with _suppress_oserror():
                loop.remove_reader(handle.master_fd)
            handle._closed = True
            handle._queue.put_nowait(None)
            return
        handle._queue.put_nowait(chunk)

    loop.add_reader(handle.master_fd, _on_readable)

    async def _watchdog() -> None:
        # Reaps the process and ensures the queue gets a None sentinel
        # even if the read path didn't EOF (e.g. process killed by
        # signal while master still open).
        await handle.proc.wait()
        if not handle._closed:
            handle._closed = True
            with _suppress_oserror():
                loop.remove_reader(handle.master_fd)
            handle._queue.put_nowait(None)

    handle._reader_task = loop.create_task(_watchdog(), name="pty-watchdog")


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


def _try_write(fd: int, data: bytes) -> int:
    """Best-effort write. Returns the byte count written, or -1 on
    OSError (caller treats negative as "stop trying")."""
    try:
        return os.write(fd, data)
    except OSError:
        return -1


class _suppress_oserror:
    """Tiny context manager — clearer than nesting `try/except`."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return exc_type is None or issubclass(exc_type, OSError)
