"""Phase G.1.d — auth job registry contract.

Tests target the in-process registry + the public lifecycle calls.
Spawning is exercised against a tiny harmless subprocess (`echo` /
a python one-liner) so we don't depend on `claude` being present
on the test host. The registry's bookkeeping is the load-bearing
contract: spawn → drain → exit-event → sentinel → reap.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from gapt_server.domains.llm_backends import auth_jobs as aj


@pytest.fixture(autouse=True)
def _reset_registry_between_tests() -> None:
    """Module-level singleton — bleed-through would make tests
    order-sensitive."""
    aj.reset_registry()
    yield
    aj.reset_registry()


@pytest.mark.asyncio
async def test_spawn_drains_stdout_into_history_and_exit_event() -> None:
    """A tiny echo subprocess: writes one line, exits 0. The job
    must capture the line and post a terminal `exit` event."""
    job = await aj.spawn_auth_job(
        kind="test_echo",
        argv=["/bin/sh", "-c", "echo hello-stdout"],
    )
    # Wait for the drain task to finish.
    assert job._writer_task is not None
    await asyncio.wait_for(job._writer_task, timeout=5.0)

    channels = [entry["channel"] for entry in job.history]
    assert "stdout" in channels
    assert channels[-1] == "exit"
    assert job.exit_code == 0
    assert job.finished_at is not None
    # Sentinel queued so an SSE consumer can close.
    sentinel = job.lines.get_nowait()
    # The queue may also have buffered the stdout + exit events the
    # consumer hasn't drained; drain until we hit the None sentinel
    # or the queue empties.
    while sentinel is not None:
        try:
            sentinel = job.lines.get_nowait()
        except asyncio.QueueEmpty:
            break


@pytest.mark.asyncio
async def test_submit_input_writes_to_subprocess_stdin() -> None:
    """A subprocess that echoes its first stdin line. Forwarding
    input must (a) reach the subprocess and (b) emit a masked
    `stdin` record into history."""
    job = await aj.spawn_auth_job(
        kind="test_cat",
        argv=["/usr/bin/env", "python3", "-c", "import sys; print('got:', sys.stdin.readline().strip())"],
    )
    await aj.submit_input(job, "DEVICECODE-SUPERSECRET-12345", append_newline=True)

    assert job._writer_task is not None
    await asyncio.wait_for(job._writer_task, timeout=5.0)
    stdouts = [e["text"] for e in job.history if e["channel"] == "stdout"]
    assert any("got: DEVICECODE-SUPERSECRET-12345" in line for line in stdouts), stdouts

    # History carries a masked stdin echo, NOT the raw token.
    stdin_records = [e for e in job.history if e["channel"] == "stdin"]
    assert len(stdin_records) == 1
    assert "DEVICECODE-S" in stdin_records[0]["text"]
    assert "SUPERSECRET" not in stdin_records[0]["text"]


@pytest.mark.asyncio
async def test_submit_input_after_finish_raises() -> None:
    """Forwarding input to a finished job is a user error (modal
    sent stale state). Must raise RuntimeError so the router can
    map it to 409."""
    job = await aj.spawn_auth_job(kind="test_echo", argv=["/bin/sh", "-c", "echo done"])
    assert job._writer_task is not None
    await asyncio.wait_for(job._writer_task, timeout=5.0)
    assert job.is_finished

    with pytest.raises(RuntimeError):
        await aj.submit_input(job, "too late")


@pytest.mark.asyncio
async def test_cancel_running_job_terminates_it() -> None:
    """A long-running subprocess gets SIGTERM'd via `cancel_job`."""
    job = await aj.spawn_auth_job(
        kind="test_sleep",
        argv=["/usr/bin/env", "python3", "-c", "import time; time.sleep(30)"],
    )
    assert not job.is_finished
    killed = aj.cancel_job(job)
    assert killed is True
    assert job._writer_task is not None
    await asyncio.wait_for(job._writer_task, timeout=5.0)
    assert job.exit_code is not None


@pytest.mark.asyncio
async def test_get_job_returns_registered_handle() -> None:
    job = await aj.spawn_auth_job(kind="test_echo", argv=["/bin/sh", "-c", "echo x"])
    assert aj.get_job(job.job_id) is job
    assert aj.get_job("does-not-exist") is None
    assert job._writer_task is not None
    await asyncio.wait_for(job._writer_task, timeout=5.0)


@pytest.mark.asyncio
async def test_spawn_unknown_binary_raises_runtime_error() -> None:
    """Caller (router) maps this to a 400. The wrapper must NOT
    leak `FileNotFoundError` to the framework."""
    with pytest.raises(RuntimeError):
        await aj.spawn_auth_job(
            kind="test_missing",
            argv=["/definitely/not/a/real/binary/xyz"],
        )


def test_reap_purges_finished_jobs_past_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force a job's `finished_at` into the deep past, run the
    reaper, expect it gone. Tests the retention bookkeeping
    without waiting an hour."""
    job = aj.AuthJob(kind="stub", argv=["x"])
    job.exit_code = 0
    job.finished_at = time.time() - (60 * 60) - 10
    aj._JOBS[job.job_id] = job
    assert aj.get_job(job.job_id) is not None

    purged = aj.reap_old_jobs()
    assert purged == 1
    assert aj.get_job(job.job_id) is None


def test_reap_keeps_fresh_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reaper must NOT drop in-flight or recently-finished
    jobs — only stale ones."""
    job = aj.AuthJob(kind="stub", argv=["x"])
    aj._JOBS[job.job_id] = job  # just started

    purged = aj.reap_old_jobs()
    assert purged == 0
    assert aj.get_job(job.job_id) is job
