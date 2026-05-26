"""Process-scoped deploy runner — decouples deploy tasks from the
HTTP request that started them.

The old flow tied a deploy directly to the `POST /deploy/stream`
SSE request: when the browser tab navigated away, the request
closed, and the orchestrator task got cancelled mid-run. State was
gone — the user couldn't come back to the Deploy tab and resume
watching.

This registry fixes that. Deploys live as named asyncio tasks on
the FastAPI process; HTTP requests just *attach* to them. Tabs can
come and go; multiple tabs can subscribe to the same run; a closed
tab leaves the task running.

Lifecycle:

  1. `registry.start(env, …)` — inserts a `DeployRun(status=pending)`
     row, spawns the background task, returns the handle. The
     calling HTTP request returns immediately with the `run_id`.
  2. Background task runs `orchestrator.deploy(…)` while polling
     the target's `_runs` state for log deltas; each delta is
     appended to the in-memory buffer AND fanned out to every
     subscriber's queue.
  3. On completion, status / bound_url / final tail are written
     back to the DB row; the handle is moved to a "recent"
     dictionary so late SSE subscribers can still replay it.
  4. `registry.subscribe(run_id)` yields the full historical log
     up to the current point, then live deltas until the run
     terminates, then a closing `done` frame.

Persistence today is in-memory + final DB tail. Mid-flight log is
NOT durable across server restarts; on startup the lifespan flips
any orphaned `pending|running` rows to `aborted` so the UI sees a
clean state.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from gapt_server.db import models
    from gapt_server.domains.audit.sink import AuditSink
    from gapt_server.domains.deploy.local import LocalComposeTarget
    from gapt_server.domains.deploy.orchestrator import DeployOrchestrator
    from gapt_server.domains.deploy.protocol import DeployResult
    from gapt_server.domains.notifications import NotificationService

logger = structlog.get_logger(__name__)


# Log buffer ceiling. The full log isn't durable today, so this is
# also the cap the late-join SSE replay sees. 256 KiB is enough for
# any reasonable docker-compose log; we drop oldest bytes when full.
_LOG_BUFFER_BYTES = 256 * 1024
# Per-subscriber queue depth. Two ticks of slack — beyond that we
# drop frames on slow clients rather than wedge the task.
_SUB_QUEUE_DEPTH = 32
# After a run terminates, keep the handle around this long so late-
# arriving SSE subscribers can still replay the final state.
_HANDLE_TTL_S = 600.0


DeployStatus = Literal["pending", "running", "success", "failed", "aborted"]


@dataclass
class _LogBuffer:
    """Bounded byte buffer with a single-writer / many-reader pattern.

    Writers append text; readers ask "give me everything from offset
    N". The buffer never grows past `cap` bytes — once full, the
    oldest bytes are evicted and `start_offset` advances. Subscribers
    track absolute byte offsets so a late join can still see the
    history that hasn't fallen off yet."""

    cap: int = _LOG_BUFFER_BYTES
    _data: bytearray = field(default_factory=bytearray)
    _start_offset: int = 0  # bytes evicted from the front

    @property
    def total_bytes(self) -> int:
        return self._start_offset + len(self._data)

    def append(self, text: str) -> None:
        chunk = text.encode("utf-8", errors="replace")
        self._data.extend(chunk)
        if len(self._data) > self.cap:
            drop = len(self._data) - self.cap
            del self._data[:drop]
            self._start_offset += drop

    def read_from(self, offset: int) -> tuple[str, int]:
        """Return (text, new_offset). `text` is everything after the
        given absolute offset that's still resident; clamps to the
        oldest available byte if the caller is way behind."""
        if offset < self._start_offset:
            offset = self._start_offset
        slice_start = offset - self._start_offset
        chunk = bytes(self._data[slice_start:])
        return chunk.decode("utf-8", errors="replace"), self.total_bytes

    def snapshot(self) -> str:
        return bytes(self._data).decode("utf-8", errors="replace")


@dataclass
class _Subscriber:
    """SSE attach-point. Each `subscribe()` call gets one.
    Equality is identity so instances live in a set."""

    queue: asyncio.Queue[bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_SUB_QUEUE_DEPTH)
    )

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


@dataclass
class DeployRunHandle:
    """Everything a UI needs to render + drive one deploy run. Held
    in `DeployRegistry` for the run's lifetime (and a grace period
    after) so detached subscribers can re-attach.

    Mutated only on the task's loop thread or under the registry
    lock; subscribers read `status` / `log_buffer` directly without
    further synchronisation."""

    run_id: str
    environment_id: str
    project_id: str
    version: str
    trigger_kind: str
    started_at: float  # monotonic; for handle eviction
    started_at_wallclock: datetime
    status: DeployStatus
    bound_url: str | None = None
    exec_code: str | None = None
    finished_at: datetime | None = None
    log_buffer: _LogBuffer = field(default_factory=_LogBuffer)
    subscribers: set[_Subscriber] = field(default_factory=set)
    # The orchestrator task, kept so we can cancel via `kill()`.
    task: asyncio.Task[Any] | None = None
    # Final orchestrator result — set on completion, useful for the
    # `done` SSE frame and `GET /deploy/runs/{id}`.
    result: "DeployResult | None" = None

    def is_terminal(self) -> bool:
        return self.status in ("success", "failed", "aborted")

    def append_log(self, text: str) -> None:
        if not text:
            return
        self.log_buffer.append(text)
        # Broadcast a `log` frame to every live subscriber. Drop the
        # oldest queued frame under back-pressure so a slow client
        # never wedges the writer.
        frame = _encode_sse(
            "log",
            {"content": text, "offset": self.log_buffer.total_bytes},
        )
        for sub in list(self.subscribers):
            self._enqueue(sub, frame)

    def broadcast_status(self) -> None:
        frame = _encode_sse(
            "status",
            {
                "run_id": self.run_id,
                "status": self.status,
                "bound_url": self.bound_url,
                "exec_code": self.exec_code,
            },
        )
        for sub in list(self.subscribers):
            self._enqueue(sub, frame)

    def broadcast_done(self) -> None:
        frame = _encode_sse(
            "done",
            {
                "run_id": self.run_id,
                "status": self.status,
                "bound_url": self.bound_url,
                "exec_code": self.exec_code,
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            },
        )
        for sub in list(self.subscribers):
            self._enqueue(sub, frame)

    @staticmethod
    def _enqueue(sub: _Subscriber, frame: bytes) -> None:
        try:
            sub.queue.put_nowait(frame)
            return
        except asyncio.QueueFull:
            pass
        # Drop the oldest pending frame so the newest one always wins.
        try:
            sub.queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            sub.queue.put_nowait(frame)
        except asyncio.QueueFull:
            pass


# Callable taking (env_row, version, target_options, two_factor_code,
# notify_fn, log_fn) and running the orchestrator. The registry doesn't
# import orchestrator/target classes directly to keep the cycle out.
DeployRunnerFn = "Callable[[DeployRunHandle], Awaitable[DeployResult]]"


class DeployAlreadyRunning(RuntimeError):
    """Raised when `start()` is called for an env that already has a
    live deploy. The user can cancel the active one first, or wait."""


class DeployRunNotFound(LookupError):
    pass


class DeployRegistry:
    """Singleton-style registry. One instance lives on AppContainer."""

    def __init__(
        self,
        *,
        session_factory: "async_sessionmaker[AsyncSession] | None",
    ) -> None:
        self._session_factory = session_factory
        # run_id → handle. Includes both live and "recently finished"
        # runs (within `_HANDLE_TTL_S`).
        self._handles: dict[str, DeployRunHandle] = {}
        # env_id → handle, for `get_active_for_env`. Pruned on
        # completion.
        self._active_by_env: dict[str, DeployRunHandle] = {}
        self._lock = asyncio.Lock()
        # Periodic eviction of terminal handles older than the TTL.
        self._sweeper_task: asyncio.Task[None] | None = None

    # ──────────────────────────────────────────── public API ──

    async def start(
        self,
        *,
        env_row: "models.Environment",
        version: str,
        actor_id: str | None,
        trigger_kind: str,
        runner: DeployRunnerFn,
    ) -> DeployRunHandle:
        """Insert a pending `DeployRun` row, spawn the orchestrator
        task, return the handle. Subsequent calls for the same env
        raise `DeployAlreadyRunning` until the active run terminates."""
        async with self._lock:
            existing = self._active_by_env.get(env_row.id)
            if existing is not None and not existing.is_terminal():
                raise DeployAlreadyRunning(existing.run_id)

            run_id = await self._persist_pending(env_row, version, actor_id, trigger_kind)
            handle = DeployRunHandle(
                run_id=run_id,
                environment_id=env_row.id,
                project_id=env_row.project_id,
                version=version,
                trigger_kind=trigger_kind,
                started_at=time.monotonic(),
                started_at_wallclock=datetime.now(tz=UTC),
                status="pending",
            )
            self._handles[run_id] = handle
            self._active_by_env[env_row.id] = handle

            # Spawn the runner. The runner closes over the orchestrator
            # + target; the registry only knows about the handle's
            # log/status mutation API.
            handle.task = asyncio.create_task(
                self._run(handle, runner, actor_id=actor_id),
                name=f"deploy-{run_id}",
            )
            if self._sweeper_task is None:
                self._sweeper_task = asyncio.create_task(self._sweep(), name="deploy-sweep")
            logger.info(
                "deploy.registry.started",
                run_id=run_id,
                env_id=env_row.id,
                version=version,
                trigger_kind=trigger_kind,
            )
        return handle

    def get(self, run_id: str) -> DeployRunHandle | None:
        return self._handles.get(run_id)

    def get_active_for_env(self, env_id: str) -> DeployRunHandle | None:
        h = self._active_by_env.get(env_id)
        if h is None or h.is_terminal():
            return None
        return h

    def list_active(self) -> list[DeployRunHandle]:
        return [h for h in self._active_by_env.values() if not h.is_terminal()]

    async def cancel(self, run_id: str) -> bool:
        async with self._lock:
            h = self._handles.get(run_id)
            if h is None:
                raise DeployRunNotFound(run_id)
            if h.is_terminal():
                return False
            if h.task is not None and not h.task.done():
                h.task.cancel()
                return True
            return False

    async def subscribe(self, run_id: str) -> AsyncIterator[bytes]:
        """SSE event source for a run. Yields:
          1. one or more `log` frames replaying everything currently in
             the buffer (so a late join sees the history),
          2. then live `log` / `status` frames as the task progresses,
          3. then exactly one `done` frame when the run terminates,
             then EOF.
        """
        handle = self._handles.get(run_id)
        if handle is None:
            raise DeployRunNotFound(run_id)
        sub = _Subscriber()
        async with self._lock:
            handle.subscribers.add(sub)
            # Send a status snapshot up front so the client knows
            # what it's looking at even if no log has streamed yet.
            yield_pending = [
                _encode_sse(
                    "status",
                    {
                        "run_id": handle.run_id,
                        "status": handle.status,
                        "bound_url": handle.bound_url,
                        "exec_code": handle.exec_code,
                    },
                )
            ]
            # Replay the in-memory log buffer.
            replay, _ = handle.log_buffer.read_from(0)
            if replay:
                yield_pending.append(
                    _encode_sse(
                        "log",
                        {"content": replay, "offset": handle.log_buffer.total_bytes},
                    )
                )
            # If the run already terminated (late join), send the
            # final `done` and don't bother waiting on the queue.
            terminal_done = (
                _encode_sse(
                    "done",
                    {
                        "run_id": handle.run_id,
                        "status": handle.status,
                        "bound_url": handle.bound_url,
                        "exec_code": handle.exec_code,
                        "finished_at": handle.finished_at.isoformat()
                        if handle.finished_at
                        else None,
                    },
                )
                if handle.is_terminal()
                else None
            )

        try:
            for frame in yield_pending:
                yield frame
            if terminal_done is not None:
                yield terminal_done
                return
            # Live tail until either the run ends OR the subscriber
            # disconnects.
            while True:
                frame = await sub.queue.get()
                yield frame
                # `done` is the last frame — bail out so we don't
                # block on a queue that will never refill.
                if frame.startswith(b"event: done"):
                    return
        finally:
            async with self._lock:
                handle.subscribers.discard(sub)

    async def aclose(self) -> None:
        """Cancel all live runs + the sweeper task. Called from app
        lifespan shutdown."""
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            self._sweeper_task = None
        for h in list(self._handles.values()):
            if h.task is not None and not h.task.done():
                h.task.cancel()
        # Don't await — server is shutting down, asyncio cleanup will
        # do it.

    # ─────────────────────────────────────────── runner driver ──

    async def _run(
        self,
        handle: DeployRunHandle,
        runner: DeployRunnerFn,
        *,
        actor_id: str | None,  # noqa: ARG002 — used by lifespan finalizer
    ) -> None:
        handle.status = "running"
        handle.broadcast_status()
        await self._persist_running(handle)

        try:
            result = await runner(handle)
            handle.result = result
            handle.bound_url = result.bound_url
            handle.exec_code = result.exec_code
            handle.status = "success" if result.status.value == "success" else "failed"
            # The runner may have already appended the final log
            # snapshot via log_fn. Just in case, splice `result.log`
            # into the buffer too — duplication is harmless.
            if result.log and result.log not in handle.log_buffer.snapshot():
                handle.append_log("\n" + result.log)
        except asyncio.CancelledError:
            handle.status = "aborted"
            handle.exec_code = "deploy.cancelled"
            handle.append_log("\n[cancelled by operator]\n")
            raise
        except Exception as exc:  # noqa: BLE001
            handle.status = "failed"
            handle.exec_code = getattr(exc, "code", "deploy.crashed")
            handle.append_log(f"\n[orchestrator error] {exc}\n")
        finally:
            handle.finished_at = datetime.now(tz=UTC)
            await self._persist_terminal(handle)
            handle.broadcast_status()
            handle.broadcast_done()
            async with self._lock:
                # Free the env slot so a re-deploy can start.
                if self._active_by_env.get(handle.environment_id) is handle:
                    del self._active_by_env[handle.environment_id]
            logger.info(
                "deploy.registry.finished",
                run_id=handle.run_id,
                env_id=handle.environment_id,
                status=handle.status,
            )

    # ────────────────────────────────────────────── eviction ──

    async def _sweep(self) -> None:
        """Drop terminal handles older than the TTL so memory doesn't
        creep over a long-running server's lifetime."""
        try:
            while True:
                await asyncio.sleep(60.0)
                cutoff = time.monotonic() - _HANDLE_TTL_S
                async with self._lock:
                    stale = [
                        rid
                        for rid, h in self._handles.items()
                        if h.is_terminal() and h.started_at < cutoff
                    ]
                    for rid in stale:
                        del self._handles[rid]
        except asyncio.CancelledError:
            return

    # ──────────────────────────────────────────── DB writes ──

    async def _persist_pending(
        self,
        env_row: "models.Environment",
        version: str,
        actor_id: str | None,
        trigger_kind: str,
    ) -> str:
        from gapt_server.db import models  # noqa: PLC0415 — avoid cycle
        from gapt_server.db.ulid import new_ulid  # noqa: PLC0415

        run_id = new_ulid()
        if self._session_factory is None:
            return run_id
        async with self._session_factory() as db:
            run = models.DeployRun(
                id=run_id,
                environment_id=env_row.id,
                version=version,
                status="pending",
                bound_url=None,
                exec_code=None,
                log_tail="",
                finished_at=None,
                actor_id=actor_id,
                trigger_kind=trigger_kind,
            )
            db.add(run)
            await db.commit()
        return run_id

    async def _persist_running(self, handle: DeployRunHandle) -> None:
        if self._session_factory is None:
            return
        await self._patch_run(
            handle.run_id,
            {"status": "running"},
        )

    async def _persist_terminal(self, handle: DeployRunHandle) -> None:
        if self._session_factory is None:
            return
        tail = handle.log_buffer.snapshot()
        if len(tail) > 4096:
            tail = "…" + tail[-4096:]
        await self._patch_run(
            handle.run_id,
            {
                "status": handle.status,
                "bound_url": handle.bound_url,
                "exec_code": handle.exec_code,
                "log_tail": tail,
                "finished_at": handle.finished_at,
            },
        )
        # On success, also update the env's denormalised last_run.
        if handle.status == "success":
            await self._update_env_last_run(handle)

    async def _patch_run(self, run_id: str, fields: dict[str, Any]) -> None:
        from gapt_server.db import models  # noqa: PLC0415

        if self._session_factory is None:
            return
        async with self._session_factory() as db:
            row = await db.get(models.DeployRun, run_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            await db.commit()

    async def _update_env_last_run(self, handle: DeployRunHandle) -> None:
        from gapt_server.db import models  # noqa: PLC0415

        if self._session_factory is None:
            return
        async with self._session_factory() as db:
            env = await db.get(models.Environment, handle.environment_id)
            if env is None:
                return
            env.last_run = {
                "run_id": handle.run_id,
                "status": handle.status,
                "bound_url": handle.bound_url,
                "version": handle.version,
                "deployed_at": handle.finished_at.isoformat()
                if handle.finished_at
                else None,
                "trigger_kind": handle.trigger_kind,
            }
            await db.commit()


# ─────────────────────────────────────────── orphan cleanup ──


async def mark_orphan_runs_aborted(
    session_factory: "async_sessionmaker[AsyncSession]",
) -> int:
    """On server startup: any `DeployRun` row in pending/running state
    has no live task driving it (the process they belonged to is
    gone). Flip them to `aborted` so the UI doesn't show forever-
    spinning runs. Returns the count flipped."""
    from sqlalchemy import or_, update  # noqa: PLC0415

    from gapt_server.db import models  # noqa: PLC0415

    async with session_factory() as db:
        stmt = (
            update(models.DeployRun)
            .where(or_(models.DeployRun.status == "pending", models.DeployRun.status == "running"))
            .values(
                status="aborted",
                finished_at=datetime.now(tz=UTC),
                exec_code="deploy.server_restart",
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0


# ──────────────────────────────────────────── log helper ──


def make_log_pump(
    handle: DeployRunHandle,
    target: "LocalComposeTarget",
) -> "Callable[[], Awaitable[None]]":
    """Returns a coroutine that polls the target's per-run log_tail
    and pushes deltas into the handle's buffer. The runner spawns
    this in parallel with `target.deploy(...)`.

    The target keeps its own short-lived per-run state under
    `target._runs[run_id]`, indexed by an *internal* run_id that may
    differ from our DeployRun.id. We pick the first key that appears
    after deploy.start (only one run per env at a time so this is
    unambiguous)."""

    async def pump() -> None:
        last_log = ""
        last_status: str | None = None
        # Wait briefly for the target to register its internal run.
        internal_id: str | None = None
        for _ in range(40):
            if target._runs:  # noqa: SLF001
                internal_id = next(iter(target._runs.keys()))  # noqa: SLF001
                break
            await asyncio.sleep(0.1)
        while True:
            if internal_id is None or internal_id not in target._runs:  # noqa: SLF001
                await asyncio.sleep(0.2)
                continue
            state = target._runs[internal_id]  # noqa: SLF001
            if state.log_tail != last_log:
                delta = state.log_tail[len(last_log):]
                last_log = state.log_tail
                if delta:
                    handle.append_log(delta)
            new_status = state.status.value
            if new_status != last_status:
                last_status = new_status
                # We don't surface target-internal status to the
                # outer status field (that one tracks orchestrator-
                # level success/failed/etc) — but the log already
                # captures the transitions.
            if state.status.value in ("success", "failed"):
                return
            await asyncio.sleep(0.3)

    return pump


# ─────────────────────────────────────────── SSE encoding ──


def _encode_sse(event: str, data: object) -> bytes:
    body = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")
