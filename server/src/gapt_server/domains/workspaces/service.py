"""Workspace service — creates a sandbox + DB row in lockstep.

Lifecycle the M1 caller can ask for:

  create  → status = creating → sandbox.create → start → wait_for_daemon
            → host-side git clone (best-effort)
            → status = running ; on failure → status = failed
  stop    → sandbox.stop                       → status = stopped
  start   → sandbox.start + wait_for_daemon    → status = running
  delete  → sandbox.destroy                    → status = archived

Host-side clone runs `git clone --depth=1` against the project's
git_remote_url so the worktree directory has real files even when the
sandbox backend is the mock (dev path) or when the sandbox-side daemon
hasn't shipped yet. With the Sysbox backend the same path is mounted
into the container so the agent sees the files too.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import AuditAction, AuditEvent, AuditSink, NullAuditSink
from gapt_server.domains.projects.service import fetch_project_for
from gapt_server.domains.sandbox import (
    SandboxBackend,
    SandboxBackendError,
    SandboxCreateSpec,
    SandboxRef,
    SandboxResources,
    SecurityInvariantError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


CloneRunner = Callable[[str, str, str], Awaitable[tuple[int, str, str]]]
"""Signature: (git_remote_url, branch, dest_dir) → (exit_code, stdout, stderr)."""


_CLONE_RETRIES = 3
# 10 min per attempt — large-asset repos over a thin link routinely need
# 3-5 min for the receive step; 180s clipped legitimate slow clones.
_CLONE_TIMEOUT_S = 600.0
# Clone log lives as a SIBLING of the worktree dir, not inside it —
# git clone refuses any non-empty destination, so we can't drop the
# log file in there or git fails before downloading the first byte.
# Layout:
#   /workspace/<slug>/<wid>           ← worktree (git clones here)
#   /workspace/<slug>/.<wid>.clone.log ← progress log (this file)
_CLONE_LOG_SUFFIX = ".clone.log"
_CLONE_LOG_MAX_BYTES = 256 * 1024  # 256 KB tail


def clone_log_path(worktree: str) -> str:
    """Return the absolute path to the clone log for a worktree.

    Sibling of the worktree dir so git can clone into an empty
    destination while the log is still preserved across retries."""
    normalised = worktree.rstrip("/")
    parent = os.path.dirname(normalised) or "/"
    name = os.path.basename(normalised)
    return os.path.join(parent, f".{name}{_CLONE_LOG_SUFFIX}")


def _check_worktree(worktree: str) -> tuple[str, str | None]:
    """Sync helper: ensure parent dir exists and decide whether the
    worktree needs a fresh clone, a resume (skip), or is unreachable.
    Returns `(verdict, detail)` where `verdict` is `proceed | exists |
    error`."""
    parent = os.path.dirname(worktree.rstrip("/"))
    try:
        os.makedirs(parent or "/", exist_ok=True)
    except OSError as exc:
        return ("error", f"mkdir parent failed: {exc}")
    if os.path.isdir(worktree):
        try:
            existing = [name for name in os.listdir(worktree) if not name.startswith(".gapt-")]
        except OSError as exc:
            return ("error", f"listdir failed: {exc}")
        if existing:
            return ("exists", f"{len(existing)} entries already present")
    return ("proceed", None)


def _append_clone_log(worktree: str, line: str) -> None:
    """Append one line to the worktree's clone log file. Silently
    rotates when the file gets large so a misbehaving git progress
    doesn't fill the disk."""
    path = clone_log_path(worktree)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Rotate at threshold — keep only the latest half.
        if os.path.isfile(path) and os.path.getsize(path) > _CLONE_LOG_MAX_BYTES:
            with open(path, "rb") as fh:
                tail = fh.read()[-_CLONE_LOG_MAX_BYTES // 2 :]
            with open(path, "wb") as fh:
                fh.write(tail)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            if not line.endswith("\n"):
                fh.write("\n")
    except OSError:
        pass


async def _stream_subprocess(
    cmd: list[str], worktree: str, attempt: int
) -> tuple[int, str]:
    """Run git clone with `--progress` and tee stdout+stderr lines to
    the clone log file. Returns `(exit_code, last_err_line)`. The line
    streaming is what makes the UI feel live — git's `--progress`
    emits one carriage-return-terminated update per kilobyte transferred
    so the user sees real movement."""
    _append_clone_log(
        worktree,
        f"\n──── attempt {attempt} @ {datetime.now(tz=UTC).isoformat(timespec='seconds')} ────",
    )
    _append_clone_log(worktree, "$ " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    last_line = ""

    async def reader() -> None:
        nonlocal last_line
        assert proc.stdout is not None
        # git emits progress with \r terminators; readuntil on either \n
        # or \r captures every update. Use raw byte read + splitlines.
        buf = b""
        while True:
            chunk = await proc.stdout.read(256)
            if not chunk:
                if buf:
                    text = buf.decode("utf-8", errors="replace")
                    _append_clone_log(worktree, text)
                    last_line = text.strip().splitlines()[-1] if text.strip() else last_line
                return
            buf += chunk
            # Replace \r → \n so each git progress update becomes its
            # own line in the log file.
            text = buf.replace(b"\r", b"\n").decode("utf-8", errors="replace")
            *lines, partial = text.split("\n")
            for ln in lines:
                if ln.strip():
                    _append_clone_log(worktree, ln)
                    last_line = ln
            buf = partial.encode("utf-8")

    await reader()
    await proc.wait()
    rc = proc.returncode if proc.returncode is not None else -1
    _append_clone_log(worktree, f"── exit {rc} ──")
    return rc, last_line


async def _default_clone_runner(
    git_remote_url: str, branch: str, dest_dir: str
) -> tuple[int, str, str]:
    """Shallow-clone the project's remote into `dest_dir` with retries.

    Streams git's `--progress` output to `{dest_dir}/.gapt-clone.log`
    so the UI can show live progress. Each attempt runs with an overall
    timeout; the asyncio wait_for kills git if it stalls. HTTP/1.1 is
    forced because GitHub's HTTP/2 stream cancels are the most common
    cause of mid-clone hangs on thin links."""
    bin_path = shutil.which("git") or "/usr/bin/git"
    last_err = ""
    last_code = -1
    for attempt in range(1, _CLONE_RETRIES + 1):
        # Drop the destination entirely between retries. Git wants an
        # empty dest, so we remove any partial state from prior
        # attempts (incl. .git that failed mid-fetch).
        await asyncio.to_thread(shutil.rmtree, dest_dir, True)
        cmd = [
            bin_path,
            "-c", "http.version=HTTP/1.1",
            "-c", "http.postBuffer=524288000",
            "-c", "http.lowSpeedLimit=1024",
            "-c", "http.lowSpeedTime=30",
            "clone",
            "--progress",
            "--depth=1",
            "--no-tags",
            f"--branch={branch}" if branch else "",
            git_remote_url,
            dest_dir,
        ]
        cmd = [arg for arg in cmd if arg]
        try:
            last_code, last_err = await asyncio.wait_for(
                _stream_subprocess(cmd, dest_dir, attempt),
                timeout=_CLONE_TIMEOUT_S,
            )
        except TimeoutError:
            _append_clone_log(
                dest_dir,
                f"!! attempt {attempt} timed out after {_CLONE_TIMEOUT_S}s — killing git",
            )
            last_err = f"git clone timed out after {_CLONE_TIMEOUT_S}s (attempt {attempt})"
            last_code = -2
            continue
        except OSError as exc:
            last_err = f"git subprocess failed: {exc}"
            last_code = -3
            break
        if last_code == 0:
            return (0, "", last_err)
        if any(
            marker in last_err
            for marker in (
                "Repository not found",
                "Authentication failed",
                "could not read Username",
                "fatal: unable to access",
                "Could not resolve host",
            )
        ):
            break
    return (last_code, "", last_err)

logger = structlog.get_logger(__name__)


class WorkspaceError(RuntimeError):
    """Domain error — carries a stable code suffix."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceView:
    id: str
    project_id: str
    branch: str
    worktree_path: str
    sandbox_id: str | None
    status: enums.WorkspaceStatus
    last_activity_at: datetime
    created_at: datetime


def _view(row: models.Workspace) -> WorkspaceView:
    return WorkspaceView(
        id=row.id,
        project_id=row.project_id,
        branch=row.branch,
        worktree_path=row.worktree_path,
        sandbox_id=row.sandbox_id,
        status=row.status,
        last_activity_at=row.last_activity_at,
        created_at=row.created_at,
    )


class WorkspaceService:
    def __init__(
        self,
        *,
        sandbox_backend: SandboxBackend,
        sandbox_image: str,
        audit_sink: AuditSink | None = None,
        clone_runner: CloneRunner | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._sandbox = sandbox_backend
        self._image = sandbox_image
        self._audit: AuditSink = audit_sink or NullAuditSink()
        self._clone: CloneRunner = clone_runner or _default_clone_runner
        # When set, workspace.create spawns the clone as a background
        # task using a fresh session from this factory. Tests omit it
        # and get the synchronous in-request clone path.
        self._session_factory = session_factory
        # Track in-flight clone tasks so tests + shutdown can join them.
        self._clone_tasks: set[asyncio.Task[None]] = set()

    # ────────────────────────────────────────────────────── create ──

    async def create(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        project_id: str,
        branch: str,
        worktree_path: str | None = None,
    ) -> WorkspaceView:
        project = await fetch_project_for(db, actor=actor, project_id=project_id)
        workspace_id = new_ulid()
        worktree = worktree_path or f"/workspace/{project.slug}/{workspace_id}"
        row = models.Workspace(
            id=workspace_id,
            project_id=project_id,
            branch=branch,
            worktree_path=worktree,
            status=enums.WorkspaceStatus.CREATING,
        )
        db.add(row)
        await db.flush()

        # Sandbox boot is best-effort; failure flips the row to FAILED
        # but keeps it around so the user can diagnose / retry.
        try:
            sandbox_ref = await self._boot_sandbox(project_id=project_id, workspace_id=workspace_id)
        except (SandboxBackendError, SecurityInvariantError) as exc:
            row.status = enums.WorkspaceStatus.FAILED
            await db.flush()
            await self._audit.log(
                AuditEvent(
                    action=AuditAction.WORKSPACE_CREATE,
                    actor_type=enums.AuditActorType.USER,
                    actor_id=actor.id,
                    outcome=enums.AuditOutcome.ERROR,
                    scope={"project_id": project_id, "workspace_id": workspace_id},
                    payload={"error": str(exc)},
                )
            )
            raise WorkspaceError(
                "workspace.sandbox_boot_failed", f"sandbox boot failed: {exc}"
            ) from exc

        sandbox_row = models.Sandbox(
            id=sandbox_ref.id,
            project_id=project_id,
            workspace_id=workspace_id,
            status=enums.SandboxStatus.RUNNING,
            container_id=sandbox_ref.container_id,
            image_tag=self._image,
        )
        db.add(sandbox_row)
        row.sandbox_id = sandbox_ref.id
        # Keep status=CREATING while the host-side clone runs. The
        # background task flips it to RUNNING on success or FAILED
        # otherwise. If we don't have a session_factory (test path)
        # the clone runs in-request and we flip status synchronously.
        row.status = enums.WorkspaceStatus.CREATING
        row.last_activity_at = _now()
        await db.flush()

        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_CREATE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": project_id, "workspace_id": workspace_id},
                subject={"branch": branch, "worktree_path": worktree},
                payload={
                    "sandbox_id": sandbox_ref.id,
                    "image": self._image,
                    "clone": "pending",
                },
            )
        )

        if self._session_factory is not None:
            # Spawn the clone *after* this transaction commits so the
            # workspace row is visible to the background session.
            task = asyncio.create_task(
                self._background_clone(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    actor_id=actor.id,
                    worktree=worktree,
                    branch=branch,
                    git_remote_url=project.git_remote_url,
                ),
                name=f"workspace-clone-{workspace_id}",
            )
            self._clone_tasks.add(task)
            task.add_done_callback(self._clone_tasks.discard)
        else:
            # Synchronous path used by tests + scripts without an
            # async session factory wired. Flips the status here.
            clone_outcome, clone_detail = await self._safe_prepare_worktree(
                worktree=worktree,
                branch=branch,
                git_remote_url=project.git_remote_url,
            )
            row.status = (
                enums.WorkspaceStatus.RUNNING
                if clone_outcome in ("cloned", "exists", "skipped")
                else enums.WorkspaceStatus.FAILED
            )
            row.last_activity_at = _now()
            await db.flush()
            await self._audit.log(
                AuditEvent(
                    action=AuditAction.WORKSPACE_CREATE,
                    actor_type=enums.AuditActorType.SYSTEM,
                    actor_id=None,
                    outcome=(
                        enums.AuditOutcome.OK
                        if row.status is enums.WorkspaceStatus.RUNNING
                        else enums.AuditOutcome.ERROR
                    ),
                    scope={"project_id": project_id, "workspace_id": workspace_id},
                    payload={"clone": clone_outcome, "clone_detail": clone_detail},
                )
            )
        return _view(row)

    async def _safe_prepare_worktree(
        self,
        *,
        worktree: str,
        branch: str,
        git_remote_url: str,
    ) -> tuple[str, str | None]:
        try:
            return await self._prepare_worktree(
                worktree=worktree, branch=branch, git_remote_url=git_remote_url
            )
        except Exception as exc:
            logger.exception("workspace.clone.crashed", worktree=worktree)
            return ("error", f"{type(exc).__name__}: {exc}"[:500])

    async def _background_clone(
        self,
        *,
        workspace_id: str,
        project_id: str,
        actor_id: str,
        worktree: str,
        branch: str,
        git_remote_url: str,
    ) -> None:
        """Run the clone in the background + flip workspace.status when
        done. Opens a fresh DB session because the request session has
        already closed by the time we get here."""
        logger.info(
            "workspace.clone.start",
            workspace_id=workspace_id,
            worktree=worktree,
            remote=git_remote_url,
        )
        outcome, detail = await self._safe_prepare_worktree(
            worktree=worktree, branch=branch, git_remote_url=git_remote_url
        )
        new_status = (
            enums.WorkspaceStatus.RUNNING
            if outcome in ("cloned", "exists", "skipped")
            else enums.WorkspaceStatus.FAILED
        )
        if self._session_factory is None:
            return
        async with self._session_factory() as bg_db:
            row = await bg_db.get(models.Workspace, workspace_id)
            if row is not None:
                row.status = new_status
                row.last_activity_at = _now()
                await bg_db.commit()
        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_CREATE,
                actor_type=enums.AuditActorType.SYSTEM,
                actor_id=actor_id,
                outcome=(
                    enums.AuditOutcome.OK
                    if new_status is enums.WorkspaceStatus.RUNNING
                    else enums.AuditOutcome.ERROR
                ),
                scope={"project_id": project_id, "workspace_id": workspace_id},
                payload={"clone": outcome, "clone_detail": detail},
            )
        )
        logger.info(
            "workspace.clone.done",
            workspace_id=workspace_id,
            outcome=outcome,
            status=new_status.value,
        )

    async def aclose(self) -> None:
        """Wait for in-flight clone tasks to finish — used by the
        container shutdown so we don't leak running git subprocesses
        across server restarts."""
        if not self._clone_tasks:
            return
        await asyncio.gather(*self._clone_tasks, return_exceptions=True)

    async def _prepare_worktree(
        self,
        *,
        worktree: str,
        branch: str,
        git_remote_url: str,
    ) -> tuple[str, str | None]:
        """Clone the project remote into `worktree`.

        Only ensures the *parent* directory exists — git refuses any
        non-empty destination, so the worktree itself must be empty
        (or missing). If the worktree already exists and is non-empty,
        we treat it as a resume case and skip clone.

        Returns `(outcome, detail)`:
        - `("cloned", None)` on success.
        - `("exists", "<reason>")` when the dir was already non-empty.
        - `("error", "<stderr>")` when git failed.
        - `("skipped", "<reason>")` when we don't even try.
        """
        if not git_remote_url:
            return ("skipped", "git_remote_url empty")

        outcome, detail = await asyncio.to_thread(
            _check_worktree, worktree
        )
        if outcome != "proceed":
            return (outcome, detail)

        exit_code, _stdout, stderr = await self._clone(git_remote_url, branch, worktree)
        if exit_code != 0:
            return ("error", stderr.strip()[-400:] or f"git clone exit={exit_code}")
        return ("cloned", None)

    async def _boot_sandbox(self, *, project_id: str, workspace_id: str) -> SandboxRef:
        spec = SandboxCreateSpec(
            project_id=project_id,
            workspace_id=workspace_id,
            image=self._image,
            resources=SandboxResources(),
        )
        ref = await self._sandbox.create(spec)
        await self._sandbox.start(ref)
        return ref

    # ───────────────────────────────────────────────────── read ──

    async def list_for_project(
        self, db: AsyncSession, *, actor: models.User, project_id: str
    ) -> list[WorkspaceView]:
        await fetch_project_for(db, actor=actor, project_id=project_id)
        rows = (
            (
                await db.execute(
                    select(models.Workspace)
                    .where(models.Workspace.project_id == project_id)
                    .order_by(models.Workspace.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_view(r) for r in rows]

    async def get(
        self, db: AsyncSession, *, actor: models.User, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(db, actor=actor, workspace_id=workspace_id)
        return _view(row)

    # ──────────────────────────────────────────────────── mutate ──

    async def stop(
        self, db: AsyncSession, *, actor: models.User, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id, min_role=enums.Role.EDITOR
        )
        # Swallow "unknown sandbox" — the workspace row outlives the
        # in-memory mock sandbox across server restarts. Stopping a
        # workspace whose sandbox has been swept away is still a
        # legitimate "now stopped" transition from the user's view.
        await self._sandbox_for(row, action="stop", swallow_missing=True)
        row.status = enums.WorkspaceStatus.STOPPED
        row.last_activity_at = _now()
        await db.flush()
        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_STOP,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": row.project_id, "workspace_id": workspace_id},
            )
        )
        return _view(row)

    async def start(
        self, db: AsyncSession, *, actor: models.User, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id, min_role=enums.Role.EDITOR
        )
        # Same idempotency as stop — if the sandbox was swept, the
        # start request becomes a no-op + status flip. The next time
        # the user does real work the workspace boot path will
        # recreate the sandbox.
        await self._sandbox_for(row, action="start", swallow_missing=True)
        row.status = enums.WorkspaceStatus.RUNNING
        row.last_activity_at = _now()
        await db.flush()
        return _view(row)

    async def delete(
        self, db: AsyncSession, *, actor: models.User, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id, min_role=enums.Role.ADMIN
        )
        await self._sandbox_for(row, action="destroy", swallow_missing=True)
        row.status = enums.WorkspaceStatus.ARCHIVED
        row.last_activity_at = _now()
        await db.flush()
        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_DELETE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": row.project_id, "workspace_id": workspace_id},
            )
        )
        return _view(row)

    # ──────────────────────────────────────────────── internals ──

    async def _sandbox_for(
        self,
        row: models.Workspace,
        *,
        action: str,
        swallow_missing: bool = False,
    ) -> None:
        if row.sandbox_id is None:
            if swallow_missing:
                return
            raise WorkspaceError(
                "workspace.no_sandbox", f"workspace {row.id} has no attached sandbox"
            )
        ref = SandboxRef(id=row.sandbox_id, container_id=None, backend=self._sandbox.name)
        try:
            if action == "start":
                await self._sandbox.start(ref)
            elif action == "stop":
                await self._sandbox.stop(ref)
            elif action == "destroy":
                await self._sandbox.destroy(ref)
            else:  # pragma: no cover — defensive
                raise WorkspaceError("workspace.bad_action", f"unknown action {action!r}")
        except SandboxBackendError as exc:
            if swallow_missing:
                logger.warning(
                    "workspace.sandbox_action_swallowed",
                    workspace_id=row.id,
                    action=action,
                    error=str(exc),
                )
                return
            raise WorkspaceError(
                "workspace.sandbox_action_failed",
                f"sandbox {action} failed: {exc}",
            ) from exc

    async def _fetch(
        self,
        db: AsyncSession,
        *,
        actor: models.User,
        workspace_id: str,
        min_role: enums.Role = enums.Role.VIEWER,
    ) -> models.Workspace:
        row = (
            await db.execute(select(models.Workspace).where(models.Workspace.id == workspace_id))
        ).scalar_one_or_none()
        if row is None:
            raise WorkspaceError("workspace.not_found", f"workspace_id={workspace_id}")

        # Reuse the project authorisation gate.
        await fetch_project_for(db, actor=actor, project_id=row.project_id, min_role=min_role)
        return row


def _now() -> datetime:
    return datetime.now(tz=UTC)
