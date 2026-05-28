"""In-process registry of in-flight CLI auth subprocesses.

Ported from Geny's `_AuthJob` mechanism. Single-admin model = one
operator + a small handful of concurrent device-code flows at most,
so an in-memory dict + age-reaping (1h) is enough. A restart drops
the registry — the spawned `claude auth login` either exits cleanly
or the operator notices the dead modal and retries.

Why subprocess + stdin instead of just dropping the user into a
terminal:
  - `claude auth login` prints the device URL to stdout then *waits
    for the auth code on stdin*. We need to forward what the user
    types in the modal back to the subprocess.
  - Streaming stdout/stderr live to the modal lets the user see the
    "Visit https://..." line the moment it appears, instead of
    waiting for the subprocess to finish.

The `_AuthJob` keeps a bounded queue (sentinel-terminated) so a
slow SSE consumer doesn't block the subprocess drain.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


_JOB_RETENTION_S = 60 * 60  # 1h
_HISTORY_LIMIT = 2_000
_QUEUE_BOUND = 512


@dataclass
class AuthJob:
    """One in-flight subprocess + its drain state. Public surface
    used by the router endpoints in `routers/llm_backends.py`."""

    kind: str  # "claude_code_login" / "claude_code_console_login"
    argv: list[str]
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.time)
    proc: asyncio.subprocess.Process | None = None
    lines: asyncio.Queue[dict[str, Any] | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_BOUND)
    )
    history: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int | None = None
    finished_at: float | None = None
    _writer_task: asyncio.Task[None] | None = None

    @property
    def is_finished(self) -> bool:
        return self.exit_code is not None

    async def push(self, payload: dict[str, Any]) -> None:
        # Cap history so a runaway CLI can't OOM us.
        self.history.append(payload)
        if len(self.history) > _HISTORY_LIMIT:
            del self.history[: len(self.history) - _HISTORY_LIMIT]
        try:
            self.lines.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the live frame rather than block the subprocess.
            # The history list still has everything for replay.
            pass


# Module-singleton — the registry is per-process by design.
_JOBS: dict[str, AuthJob] = {}


def reap_old_jobs(now: float | None = None) -> int:
    """Drop jobs whose start (or finish) is older than the retention
    window. Returns the count purged so tests can assert."""
    cutoff = (now if now is not None else time.time()) - _JOB_RETENTION_S
    purged = 0
    for jid, job in list(_JOBS.items()):
        anchor = job.finished_at if job.finished_at is not None else job.started_at
        if anchor < cutoff:
            _JOBS.pop(jid, None)
            purged += 1
    return purged


def get_job(job_id: str) -> AuthJob | None:
    return _JOBS.get(job_id)


def list_jobs() -> list[AuthJob]:
    """Snapshot of every registered job — used by the GET status
    endpoint and tests."""
    return list(_JOBS.values())


async def spawn_auth_job(kind: str, argv: list[str]) -> AuthJob:
    """Spawn the subprocess, register it, kick off the drain task,
    return the handle.

    `start_new_session=True` puts the child in its own process group
    so we can `killpg` it on cancel. Without that, `claude auth
    login` can leave a curl child behind when the user backs out.
    """
    reap_old_jobs()
    job = AuthJob(kind=kind, argv=list(argv))
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"binary not found: {exc}") from exc
    job.proc = proc
    _JOBS[job.job_id] = job
    job._writer_task = asyncio.create_task(_drain_subprocess(job))
    return job


async def _drain_subprocess(job: AuthJob) -> None:
    """Copy the subprocess's stdout / stderr into the job's queue +
    history, post a final `exit` event, then put the sentinel `None`
    so the SSE generator can close cleanly."""
    proc = job.proc
    assert proc is not None

    async def _drain(stream: asyncio.StreamReader | None, channel: str) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.readline()
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
            await job.push({"channel": channel, "text": text, "ts": time.time()})

    await asyncio.gather(
        _drain(proc.stdout, "stdout"),
        _drain(proc.stderr, "stderr"),
    )
    rc = await proc.wait()
    job.exit_code = rc
    job.finished_at = time.time()
    await job.push(
        {
            "channel": "exit",
            "text": f"exit code {rc}",
            "ts": time.time(),
            "exit_code": rc,
        }
    )
    try:
        job.lines.put_nowait(None)
    except asyncio.QueueFull:
        pass


async def submit_input(job: AuthJob, text: str, *, append_newline: bool = True) -> None:
    """Forward a line of user input (the device-code auth code) to
    the subprocess's stdin. Echoes a masked record into the job
    history so the modal's console pane reflects what was sent
    without permanently logging a long-lived credential.
    """
    if job.proc is None or job.is_finished:
        raise RuntimeError("job already finished")
    stdin = job.proc.stdin
    if stdin is None or stdin.is_closing():
        raise RuntimeError("stdin not available")
    payload = text + ("\n" if append_newline else "")
    stdin.write(payload.encode("utf-8"))
    await stdin.drain()
    masked = text[:12] + ("…" if len(text) > 12 else "")
    await job.push(
        {
            "channel": "stdin",
            "text": f"(submitted {len(text)} chars: {masked})",
            "ts": time.time(),
        }
    )


def cancel_job(job: AuthJob) -> bool:
    """Send SIGTERM to the subprocess's process group. Returns True
    when the kill was attempted; False when the job was already
    finished / never properly started."""
    if job.proc is None or job.is_finished:
        return False
    try:
        os.killpg(os.getpgid(job.proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


def reset_registry() -> None:
    """Test-only — forget every registered job. Production code
    relies on the age-reaper instead."""
    _JOBS.clear()
