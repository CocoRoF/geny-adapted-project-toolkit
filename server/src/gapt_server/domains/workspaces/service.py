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
import contextlib
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


def _wipe_dir(path: str) -> None:
    if not os.path.isdir(path):
        return
    for entry in os.listdir(path):
        target = os.path.join(path, entry)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                os.remove(target)


async def _default_clone_runner(
    git_remote_url: str, branch: str, dest_dir: str
) -> tuple[int, str, str]:
    """Shallow-clone the project's remote into `dest_dir` with retries.

    Retries cover transient network failures (HTTP/2 stream cancels,
    early EOF, etc.) — common when cloning large repos over flaky
    links. Each attempt forces HTTP/1.1 because libgit2/curl's HTTP/2
    interaction with GitHub is the usual culprit. `dest_dir` is
    expected to exist; we wipe it between attempts so git starts
    clean. Public repos only for now — token wiring lands in M2."""
    bin_path = shutil.which("git") or "/usr/bin/git"
    last_out = ""
    last_err = ""
    last_code = -1
    for attempt in range(1, _CLONE_RETRIES + 1):
        # Wipe + recreate so retried git always starts from an empty dir.
        # os.path/listdir calls below are sync; ruff ASYNC240 wants
        # anyio.Path but the syscalls are nanoseconds and bringing
        # anyio just for the wipe is overkill — disable the lint.
        await asyncio.to_thread(_wipe_dir, dest_dir)
        cmd = [
            bin_path,
            "-c", "http.version=HTTP/1.1",
            "-c", "http.postBuffer=524288000",
            "clone",
            "--depth=1",
            "--no-tags",
            f"--branch={branch}" if branch else "",
            git_remote_url,
            dest_dir,
        ]
        cmd = [arg for arg in cmd if arg]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=_CLONE_TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                last_err = f"git clone timed out after {_CLONE_TIMEOUT_S}s (attempt {attempt})"
                last_code = -2
                continue
        except OSError as exc:
            last_err = f"git subprocess failed: {exc}"
            last_code = -3
            break
        last_code = proc.returncode if proc.returncode is not None else -1
        last_out = out.decode("utf-8", errors="replace")
        last_err = err.decode("utf-8", errors="replace")
        if last_code == 0:
            return (0, last_out, last_err)
        # Retry on partial-network errors; bail on definitive ones
        # (repo not found / auth required).
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
    return (last_code, last_out, last_err)

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
        """Create the worktree dir + clone the project remote.

        Returns `(outcome, detail)`:
        - `("cloned", None)` on success.
        - `("exists", "<reason>")` when the dir was already non-empty
          (a re-create reusing the workspace_id, or shared mount).
        - `("error", "<stderr>")` when git failed.
        - `("skipped", "<reason>")` when we don't even try (missing
          git binary, no remote URL).
        """
        if not git_remote_url:
            return ("skipped", "git_remote_url empty")
        # Idempotent: existing non-empty dir is left alone so re-creates
        # don't lose user state.
        try:
            os.makedirs(worktree, exist_ok=True)
        except OSError as exc:
            return ("error", f"mkdir failed: {exc}")
        try:
            existing = os.listdir(worktree)
        except OSError as exc:
            return ("error", f"listdir failed: {exc}")
        if existing:
            return ("exists", f"{len(existing)} entries already present")
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
        await self._sandbox_for(row, action="stop")
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
        await self._sandbox_for(row, action="start")
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
