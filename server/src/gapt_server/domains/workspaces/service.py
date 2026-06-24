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

User-scoped credentials (e.g. `github_token`) are resolved through the
optional `credentials_resolver` *before* the sandbox boots, so they
flow into the sandbox env (for in-sandbox git push / executor tools)
and into the host-side clone via `http.extraHeader=Authorization:
Basic <b64>` (token never lands in argv / `ps`).
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid
from gapt_server.domains.audit.sink import AuditAction, AuditEvent, AuditSink, NullAuditSink
from gapt_server.domains.projects import repositories as repo_service
from gapt_server.domains.projects.service import fetch_project_for
from gapt_server.domains.sandbox import (
    SandboxBackend,
    SandboxBackendError,
    SandboxCreateSpec,
    SandboxRef,
    SandboxResources,
    SecurityInvariantError,
)
from gapt_server.domains.workspaces import worktree as worktree_mod

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from gapt_server.domains.auth import AdminPrincipal
    from gapt_server.domains.workspace_sandbox import WorkspaceSandboxManager


@dataclass(frozen=True)
class RepoSpec:
    """Per-repo clone instructions consumed by the multi-repo path.

    Built from a ``ProjectRepository`` row. Carries everything the
    worktree code needs to land that repo at its target subpath
    without re-querying the DB. The empty-repo case (NULL
    ``git_remote_url``) is represented by setting ``git_remote_url``
    to None — the orchestrator then skips the clone and just makes
    sure the subdir exists.
    """

    repo_id: str
    subpath: str
    git_remote_url: str | None
    branch: str


# Stable marker for the legacy single-repo layout. When the project's
# only repository has subpath="", we preserve the historical bare
# path (`<bare_root>/<project_slug>/`) instead of nesting it under a
# repo-id segment. Lets pre-N.4 workspaces' .git files keep resolving
# to the same bare even after the schema migration.
_LEGACY_ROOT_SUBPATH = ""


def _bare_slug_for(
    project_slug: str, repo: RepoSpec, *, legacy_layout: bool
) -> str:
    """Slug passed into ``ensure_bare`` / ``add_worktree`` for one
    repo.

    Legacy single-repo projects keep the old shape
    ``<bare_root>/<project_slug>/`` so their existing workspaces'
    `.git` pointers stay valid. Multi-repo and explicit-subpath
    projects nest under ``<bare_root>/<project_slug>/<repo_id>/`` so
    multiple repos in one project don't collide.
    """
    if legacy_layout:
        return project_slug
    return f"{project_slug}/{repo.repo_id}"


def _worktree_subdir_for(worktree: str, repo: RepoSpec) -> str:
    """Where this repo's checkout lives on disk. Empty subpath = the
    workspace's worktree root (legacy single-repo); non-empty subpath
    = a folder under that root."""
    if not repo.subpath:
        return worktree
    return os.path.join(worktree, repo.subpath)


CloneRunner = Callable[[str, str, str], Awaitable[tuple[int, str, str]]]
"""Signature: (git_remote_url, branch, dest_dir) → (exit_code, stdout, stderr).

A credentials-aware variant (`_default_clone_runner_with_creds`) lives
alongside; the service prefers it when a `github_token` is resolved and
the configured runner is the default (lets tests inject a 3-arg stub
without needing to mirror the credential signature)."""

CredentialResolver = Callable[[str, str], Awaitable[dict[str, str]]]
"""(actor_id, project_id) → {"github_token": "...", "openai_api_key": "...", ...}.

The map is *also* mirrored into the sandbox env as UPPERCASE keys so
the executor + in-sandbox git see the same credentials as the host
clone path."""


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


def _github_basic_header(github_token: str) -> str:
    """Build the `Authorization: Basic` header value for git clone.

    GitHub accepts `x-access-token:<PAT>` over Basic auth for both
    classic and fine-grained PATs. Using an HTTP header (via
    `git -c http.extraHeader=...`) keeps the token out of argv / the
    URL and out of `ps`."""
    payload = base64.b64encode(f"x-access-token:{github_token}".encode()).decode("ascii")
    return f"Authorization: Basic {payload}"


async def _run_clone_attempts(
    git_remote_url: str,
    branch: str,
    dest_dir: str,
    *,
    extra_config: list[str] | None = None,
) -> tuple[int, str, str]:
    """Shared retry loop. `extra_config` is a flat list of `-c key=val`
    pairs prepended before `clone`; used by the credential-aware
    runner to inject `http.extraHeader=Authorization: Basic ...`."""
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
            *(extra_config or []),
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


async def _default_clone_runner(
    git_remote_url: str, branch: str, dest_dir: str
) -> tuple[int, str, str]:
    """Anonymous clone — no credentials injected. Backwards-compatible
    3-arg entrypoint that tests monkey-patch."""
    return await _run_clone_attempts(git_remote_url, branch, dest_dir, extra_config=None)


async def _default_clone_runner_with_creds(
    git_remote_url: str,
    branch: str,
    dest_dir: str,
    *,
    github_token: str | None,
) -> tuple[int, str, str]:
    """Authenticated clone variant. Injects `http.extraHeader` so the
    token never appears in argv. Falls back to the anonymous path when
    no token was resolved."""
    extra: list[str] | None = None
    if github_token:
        extra = ["-c", f"http.extraHeader={_github_basic_header(github_token)}"]
    return await _run_clone_attempts(git_remote_url, branch, dest_dir, extra_config=extra)


logger = structlog.get_logger(__name__)


class WorkspaceError(RuntimeError):
    """Domain error — carries a stable code suffix."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceSelectionView:
    """Phase N.5 — one (workspace, project repo, branch) selection.

    Mirrors a ``workspace_repositories`` row joined to its
    ``project_repositories`` parent for UI rendering. ``repository_id``
    is None if the project's repo row was archived after this
    workspace was created (FK ondelete=SET NULL); the row stays for
    audit, the UI shows it as orphaned."""

    repository_id: str | None
    subpath: str
    display_name: str
    branch: str
    git_remote_url: str | None


@dataclass(frozen=True)
class WorkspaceView:
    id: str
    project_id: str
    # Phase N.5 — user-facing identity. Replaces the legacy ``branch``
    # field; per-repo branches are surfaced via ``selections``.
    name: str
    worktree_path: str
    sandbox_id: str | None
    status: enums.WorkspaceStatus
    last_activity_at: datetime
    created_at: datetime
    selections: tuple[WorkspaceSelectionView, ...] = ()


def _view(
    row: models.Workspace,
    selections: list[WorkspaceSelectionView] | None = None,
) -> WorkspaceView:
    return WorkspaceView(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        worktree_path=row.worktree_path,
        sandbox_id=row.sandbox_id,
        status=row.status,
        last_activity_at=row.last_activity_at,
        created_at=row.created_at,
        selections=tuple(selections or ()),
    )


@dataclass(frozen=True)
class WorkspaceRepoSelectionInput:
    """Phase N.5 — operator-supplied selection at create time.

    ``repository_id`` MUST belong to the same project as the workspace
    being created — the service validates this. ``branch`` is the
    branch to clone for that repo; empty string means "the project
    repo has no remote (git init candidate) — just make the subdir".
    """

    repository_id: str
    branch: str


async def _selection_views_for(
    db: AsyncSession, *, workspace_id: str
) -> list[WorkspaceSelectionView]:
    """Load the per-workspace selections joined to their project repos.

    Used by every read-path (`_view`, list, get) so the UI sees the
    same shape everywhere. Orphaned join rows (the project repo was
    archived) come back with ``repository_id=None`` and inherit only
    the join row's stored branch — display_name + subpath come from
    a fallback "(archived)" placeholder so the UI can still render
    them without a separate code path."""
    stmt = (
        select(models.WorkspaceRepository, models.ProjectRepository)
        .outerjoin(
            models.ProjectRepository,
            models.WorkspaceRepository.project_repository_id
            == models.ProjectRepository.id,
        )
        .where(models.WorkspaceRepository.workspace_id == workspace_id)
        .order_by(models.WorkspaceRepository.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    out: list[WorkspaceSelectionView] = []
    for wr, pr in rows:
        if pr is None:
            out.append(
                WorkspaceSelectionView(
                    repository_id=None,
                    subpath="",
                    display_name="(archived)",
                    branch=wr.branch,
                    git_remote_url=None,
                )
            )
        else:
            out.append(
                WorkspaceSelectionView(
                    repository_id=pr.id,
                    subpath=pr.subpath,
                    display_name=pr.display_name,
                    branch=wr.branch,
                    git_remote_url=pr.git_remote_url,
                )
            )
    return out


class WorkspaceService:
    def __init__(
        self,
        *,
        sandbox_backend: SandboxBackend,
        sandbox_image: str,
        audit_sink: AuditSink | None = None,
        clone_runner: CloneRunner | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        credentials_resolver: CredentialResolver | None = None,
        workspace_sandbox: WorkspaceSandboxManager | None = None,
        max_active_sandboxes: int | None = None,
        # Where the per-project bare repos live on the host. Required
        # for the worktree clone path (production); tests that inject
        # their own `clone_runner` and never hit the worktree branch
        # can pass any string (or omit and accept the placeholder).
        workspace_bare_root: str = "/var/lib/gapt-bare",
    ) -> None:
        self._sandbox = sandbox_backend
        self._image = sandbox_image
        self._audit: AuditSink = audit_sink or NullAuditSink()
        self._clone: CloneRunner = clone_runner or _default_clone_runner
        self._bare_root = workspace_bare_root
        # Phase C.2.d — cap on concurrent live workspaces. None = no
        # cap (legacy / test default). Counted across the whole DB
        # since GAPT is single-admin.
        self._max_active = max_active_sandboxes
        # `_clone_is_default` lets us safely route to the credential-
        # aware variant only when no custom clone_runner was injected.
        # Tests inject a 3-arg stub and don't supply credentials, so
        # this stays False for them and the stub is used as-is.
        self._clone_is_default = clone_runner is None
        # When set, workspace.create spawns the clone as a background
        # task using a fresh session from this factory. Tests omit it
        # and get the synchronous in-request clone path.
        self._session_factory = session_factory
        self._credentials_resolver = credentials_resolver
        # Track in-flight clone tasks so tests + shutdown can join them.
        self._clone_tasks: set[asyncio.Task[None]] = set()
        # Optional. When provided, the background clone path also boots
        # the `gapt-ws-<wid>` container right after a successful clone
        # so the user's first hit to the IDE never sees a "container
        # not found" race. Without it, ensure() runs lazily on the
        # first fs/terminal request — still works, just adds latency.
        self._workspace_sandbox = workspace_sandbox

    # ────────────────────────────────────────────────────── create ──

    async def create(
        self,
        db: AsyncSession,
        *,
        actor: AdminPrincipal,
        project_id: str,
        name: str,
        selections: list[WorkspaceRepoSelectionInput] | None = None,
        worktree_path: str | None = None,
    ) -> WorkspaceView:
        """Phase N.5 — create a named workspace with explicit repo
        selections. ``name`` replaces the old single-branch identity;
        ``selections`` carries which of the project's repositories to
        clone and at which branch each. ``None`` selections falls back
        to "every active project repository at its default_branch" so
        the simple case still works without an explicit list.

        Idempotent on ``(project, name)``: re-creating "workspace-1"
        twice hands back the same row instead of failing. Existing
        selections are NOT updated on idempotent reuse — to change
        them, archive + recreate.
        """
        project = await fetch_project_for(db, actor=actor, project_id=project_id)
        # Creating a workspace under an archived project revives it: a project
        # with a live workspace is not "empty/dead". Hosts (Geny) reuse one
        # project for live session + sandbox-tool-pack workspaces, and GAPT can
        # auto-archive a momentarily-empty project — leaving it archived made
        # the orphan dashboard false-flag the new workspaces. Un-archive here so
        # the project + its live workspaces stay out of the orphan bucket.
        if project.archived_at is not None:
            project.archived_at = None
            await db.flush()
        name = name.strip()
        if not name:
            raise WorkspaceError("workspace.invalid_name", "name cannot be empty")
        if len(name) > 255:
            raise WorkspaceError(
                "workspace.invalid_name", "name must be ≤ 255 characters"
            )

        # Phase N.5: one *active* workspace per (project, name). If a
        # workspace with this name is already live, hand it back
        # idempotently so the UI's "open workspace X" action works
        # regardless of whether it already existed. Archived rows are
        # ignored — operator can reuse the name.
        existing_stmt = (
            select(models.Workspace)
            .where(
                models.Workspace.project_id == project_id,
                models.Workspace.name == name,
                models.Workspace.status != enums.WorkspaceStatus.ARCHIVED,
            )
            .order_by(models.Workspace.created_at.desc())
            .limit(1)
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            existing.last_activity_at = _now()
            await db.flush()
            existing_sel = await _selection_views_for(db, workspace_id=existing.id)
            return _view(existing, existing_sel)

        # Phase C.2.d — cap on concurrent live workspaces. Counts only
        # rows holding an active container (CREATING + RUNNING). The
        # idempotent-reuse branch above is intentionally exempt: opening
        # an already-live branch should never fail the cap check.
        if self._max_active is not None:
            from sqlalchemy import func as _sql_func  # noqa: PLC0415

            active_count_stmt = select(_sql_func.count()).where(
                models.Workspace.status.in_(
                    [
                        enums.WorkspaceStatus.CREATING,
                        enums.WorkspaceStatus.RUNNING,
                    ]
                )
            )
            active_count = (await db.execute(active_count_stmt)).scalar_one()
            if active_count >= self._max_active:
                raise WorkspaceError(
                    "workspace.cap_reached",
                    (
                        f"active workspace cap reached ({active_count}/"
                        f"{self._max_active}). Stop or archive an existing "
                        "workspace, or raise GAPT_MAX_ACTIVE_SANDBOXES."
                    ),
                )

        workspace_id = new_ulid()
        worktree = worktree_path or f"/workspace/{project.slug}/{workspace_id}"
        row = models.Workspace(
            id=workspace_id,
            project_id=project_id,
            name=name,
            worktree_path=worktree,
            status=enums.WorkspaceStatus.CREATING,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # Two requests created the same (project, name) concurrently
            # and lost the race to the partial unique index. Honour the
            # same idempotent contract as the existing-row branch above:
            # roll back our half-built row and hand back the winner.
            await db.rollback()
            existing = (await db.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                existing_sel = await _selection_views_for(db, workspace_id=existing.id)
                return _view(existing, existing_sel)
            raise WorkspaceError(
                "workspace.create_conflict",
                f"workspace {name!r} creation conflicted; retry",
            ) from None

        # Phase N.5 — resolve the operator's selections into project
        # repository rows + persist the join. The clone path consumes
        # the join rows (not the input list directly) so the audit /
        # rehydrate / list paths all see the same source of truth.
        repo_rows_all = await repo_service.list_for_project(
            db, project_id=project_id
        )
        repo_rows_by_id = {r.id: r for r in repo_rows_all}
        if selections is None:
            # Default: every active project repo at its default_branch
            # (empty string means "let the multi-clone fall back to
            # the remote's HEAD"). Mirrors the pre-N.5 behaviour for
            # callers that don't care to pick.
            resolved_inputs: list[WorkspaceRepoSelectionInput] = [
                WorkspaceRepoSelectionInput(
                    repository_id=r.id,
                    branch=r.default_branch or "",
                )
                for r in repo_rows_all
            ]
        else:
            resolved_inputs = list(selections)
            seen_repo_ids: set[str] = set()
            for sel in resolved_inputs:
                pr = repo_rows_by_id.get(sel.repository_id)
                if pr is None:
                    raise WorkspaceError(
                        "workspace.unknown_repository",
                        f"repository {sel.repository_id} not in project {project_id}",
                    )
                # The (workspace, repo) join carries a UNIQUE constraint.
                # Catch a repeated repository_id here with a clean 4xx
                # instead of letting the flush below blow up with an
                # IntegrityError → unhandled 500.
                if sel.repository_id in seen_repo_ids:
                    raise WorkspaceError(
                        "workspace.duplicate_repository",
                        f"repository {sel.repository_id} selected more than once",
                    )
                seen_repo_ids.add(sel.repository_id)

        join_rows: list[models.WorkspaceRepository] = []
        for sel in resolved_inputs:
            join_rows.append(
                models.WorkspaceRepository(
                    id=new_ulid(),
                    workspace_id=workspace_id,
                    project_repository_id=sel.repository_id,
                    branch=sel.branch,
                )
            )
        for jr in join_rows:
            db.add(jr)
        await db.flush()

        # Resolve user-scoped credentials *before* booting the sandbox
        # so they can flow into the container env. Failure to resolve
        # is non-fatal: anonymous clone still works for public repos.
        credentials = await self._resolve_credentials(actor_id=actor.id, project_id=project_id)

        # Sandbox boot is best-effort; failure flips the row to FAILED
        # but keeps it around so the user can diagnose / retry.
        try:
            sandbox_ref = await self._boot_sandbox(
                project_id=project_id,
                workspace_id=workspace_id,
                credentials=credentials,
            )
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
                subject={"name": name, "worktree_path": worktree},
                payload={
                    "sandbox_id": sandbox_ref.id,
                    "image": self._image,
                    "clone": "pending",
                    "selections": [
                        {"repository_id": s.repository_id, "branch": s.branch}
                        for s in resolved_inputs
                    ],
                },
            )
        )

        # Phase N.5 — fan-out clone driven by the join rows we just
        # wrote. Each join row carries the user-chosen branch (not the
        # repo's default_branch); the multi-clone orchestrator
        # resolves subpath / remote URL by joining back to the project
        # repository. Empty list = empty project (no clones, just a
        # worktree dir). Single row with subpath="" = legacy single-
        # repo (preserved bit-for-bit so existing workspaces stay
        # bare-compatible).
        repos: list[RepoSpec] = []
        for sel in resolved_inputs:
            pr = repo_rows_by_id[sel.repository_id]
            # Branch selection: prefer sel.branch (user's pick), fall back to
            # repo's default_branch, then "main" as ultimate default. Empty
            # branch strings cause git errors so we always provide something.
            branch = sel.branch or pr.default_branch or "main"
            repos.append(
                RepoSpec(
                    repo_id=pr.id,
                    subpath=pr.subpath,
                    git_remote_url=pr.git_remote_url,
                    branch=branch,
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
                    repos=repos,
                    credentials=credentials,
                    project_slug=project.slug,
                ),
                name=f"workspace-clone-{workspace_id}",
            )
            self._clone_tasks.add(task)
            task.add_done_callback(self._clone_tasks.discard)
        else:
            # Synchronous path used by tests + scripts without an
            # async session factory wired. Flips the status here.
            clone_outcome, clone_detail = await self._safe_prepare_worktree_multi(
                worktree=worktree,
                project_slug=project.slug,
                repos=repos,
                credentials=credentials,
            )
            row.status = (
                enums.WorkspaceStatus.RUNNING
                if clone_outcome in ("cloned", "exists", "skipped", "empty")
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
        selections_view = await _selection_views_for(db, workspace_id=workspace_id)
        return _view(row, selections_view)

    async def rehydrate(
        self,
        db: AsyncSession,
        *,
        actor: AdminPrincipal,
        workspace_id: str,
    ) -> tuple[WorkspaceView, str, str | None]:
        """Phase N.4 — re-run the multi-repo clone for an existing
        workspace so newly-added project repositories materialize in
        its worktree. The bare-repo / worktree-checkout invariants in
        ``_prepare_worktree_multi`` are idempotent — repos already on
        disk return ``exists`` and are skipped; missing repos clone
        into their subpath. Used by the GitPanel "이 레포는 아직
        워크스페이스에 없어요" recovery action so the operator can
        bring a stale workspace up to date without recreating it.

        Returns ``(view, outcome, detail)`` mirroring create()'s
        bookkeeping so the router can surface the clone status."""
        row = await db.get(models.Workspace, workspace_id)
        if row is None:
            raise WorkspaceError("workspace.not_found", workspace_id)
        if row.status == enums.WorkspaceStatus.ARCHIVED:
            raise WorkspaceError("workspace.archived", workspace_id)

        project = await fetch_project_for(db, actor=actor, project_id=row.project_id)
        credentials = await self._resolve_credentials(
            actor_id=actor.id, project_id=row.project_id
        )
        # Phase N.5 — drive from the workspace's join rows, NOT the
        # project's current repo list, so a workspace pinned to
        # specific branches stays pinned after rehydrate. Orphaned
        # join rows (project repo was archived) are skipped.
        join_stmt = (
            select(models.WorkspaceRepository, models.ProjectRepository)
            .join(
                models.ProjectRepository,
                models.WorkspaceRepository.project_repository_id
                == models.ProjectRepository.id,
            )
            .where(
                models.WorkspaceRepository.workspace_id == workspace_id,
                models.ProjectRepository.archived_at.is_(None),
            )
        )
        join_pairs = (await db.execute(join_stmt)).all()
        repos: list[RepoSpec] = [
            RepoSpec(
                repo_id=pr.id,
                subpath=pr.subpath,
                git_remote_url=pr.git_remote_url,
                branch=wr.branch or (pr.default_branch or ""),
            )
            for wr, pr in join_pairs
        ]
        outcome, detail = await self._safe_prepare_worktree_multi(
            worktree=row.worktree_path,
            project_slug=project.slug,
            repos=repos,
            credentials=credentials or {},
        )
        # Only flip RUNNING on success; leave the existing status alone
        # on failure so a partial outcome (some repos cloned, one
        # failed) doesn't downgrade an otherwise-healthy workspace.
        if outcome in ("cloned", "exists", "skipped", "empty"):
            row.status = enums.WorkspaceStatus.RUNNING
            row.last_activity_at = _now()
            await db.flush()
        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_CREATE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=(
                    enums.AuditOutcome.OK
                    if outcome in ("cloned", "exists", "skipped", "empty")
                    else enums.AuditOutcome.ERROR
                ),
                scope={"project_id": row.project_id, "workspace_id": workspace_id},
                payload={"rehydrate": outcome, "rehydrate_detail": detail},
            )
        )
        sel_view = await _selection_views_for(db, workspace_id=workspace_id)
        return _view(row, sel_view), outcome, detail

    async def _safe_prepare_worktree(
        self,
        *,
        worktree: str,
        branch: str,
        git_remote_url: str,
        credentials: dict[str, str] | None = None,
        project_slug: str | None = None,
    ) -> tuple[str, str | None]:
        try:
            return await self._prepare_worktree(
                worktree=worktree,
                branch=branch,
                git_remote_url=git_remote_url,
                credentials=credentials or {},
                project_slug=project_slug,
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
        repos: list[RepoSpec],
        credentials: dict[str, str] | None = None,
        project_slug: str | None = None,
    ) -> None:
        """Run the clone in the background + flip workspace.status when
        done. Opens a fresh DB session because the request session has
        already closed by the time we get here.

        Phase N.4 — the request hands us a pre-built ``repos`` list
        (one per ProjectRepository row). Empty list = empty project,
        single root-subpath = legacy single-repo, anything else =
        multi-repo. The orchestrator inside ``_prepare_worktree_multi``
        switches between those shapes; this function only owns the
        async + status / audit bookkeeping around it."""
        logger.info(
            "workspace.clone.start",
            workspace_id=workspace_id,
            worktree=worktree,
            repo_count=len(repos),
            auth=("github_token" if credentials and "github_token" in credentials else "anon"),
        )
        outcome, detail = await self._safe_prepare_worktree_multi(
            worktree=worktree,
            project_slug=project_slug or "",
            repos=repos,
            credentials=credentials or {},
        )
        new_status = (
            enums.WorkspaceStatus.RUNNING
            if outcome in ("cloned", "exists", "skipped", "empty")
            else enums.WorkspaceStatus.FAILED
        )
        if self._session_factory is None:
            return
        async with self._session_factory() as bg_db:
            # The create request commits the CREATING row *after* spawning this
            # task. For fast (empty) clones we can get here before that commit,
            # so the row isn't visible yet — retry briefly instead of silently
            # skipping the status flip (which would strand the workspace at
            # CREATING even though the container is up).
            row = None
            for _ in range(50):  # ~5s
                row = await bg_db.get(models.Workspace, workspace_id)
                if row is not None:
                    break
                await asyncio.sleep(0.1)
            if row is not None:
                row.status = new_status
                row.last_activity_at = _now()
                await bg_db.commit()
            else:
                logger.warning(
                    "workspace.clone.row_not_visible",
                    workspace_id=workspace_id,
                    note="create txn not committed within retry window; status left as-is",
                )
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
        # Proactively boot the workspace container so the user's first
        # navigation to the IDE doesn't race with a cold-start
        # `docker run`. `ensure()` is idempotent — if it's already up
        # this is just a single `docker inspect`. Best-effort: a
        # failure here doesn't roll the workspace status back (the
        # ensure path runs again on the first fs request as a safety
        # net), it just logs.
        if (
            new_status is enums.WorkspaceStatus.RUNNING
            and self._workspace_sandbox is not None
        ):
            try:
                ws_sandbox = self._workspace_sandbox.get(workspace_id, worktree)
                await ws_sandbox.ensure()
                logger.info(
                    "workspace.sandbox.preboot.ok",
                    workspace_id=workspace_id,
                    container=ws_sandbox.container_name,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort, retried lazily
                logger.warning(
                    "workspace.sandbox.preboot.failed",
                    workspace_id=workspace_id,
                    error=str(exc),
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
        credentials: dict[str, str],
        project_slug: str | None = None,
    ) -> tuple[str, str | None]:
        """Materialise the working tree for `branch` at `worktree`.

        Default path (production, Phase C.1+): a bare repo per project
        + one real `git worktree` per workspace. The bare lives at
        `<project_root>/.bare` next to the workspace dir; first
        workspace creates it via `git clone --bare`, subsequent
        workspaces just `git worktree add`.

        Legacy path (tests with `clone_runner=...` injected): keeps
        the original `git clone --depth=1 --branch=<x>` shape so the
        stub remains a 3-arg callable.

        Returns `(outcome, detail)`:
        - `("cloned", None)` on success.
        - `("exists", "<reason>")` when the dir was already non-empty
          (resume case — we treat the existing files as authoritative).
        - `("error", "<stderr>")` when git failed.
        - `("skipped", "<reason>")` when we don't even try.
        """
        if not git_remote_url:
            return ("skipped", "git_remote_url empty")

        # Resume case: a previous create attempt may have already
        # materialised the dir. We honour it regardless of layout
        # (bare-backed or legacy clone).
        outcome, detail = await asyncio.to_thread(_check_worktree, worktree)
        if outcome != "proceed":
            return (outcome, detail)

        # Default path → bare + worktree. Only when no custom clone
        # runner is injected (tests pass `clone_runner=...` to control
        # what clone does, and that stub has the legacy 3-arg shape).
        if self._clone_is_default:
            return await self._prepare_via_worktree(
                worktree=worktree,
                branch=branch,
                git_remote_url=git_remote_url,
                credentials=credentials,
                project_slug=project_slug,
            )

        # Legacy clone path (kept for the existing test surface).
        exit_code, _stdout, stderr = await self._do_clone(
            git_remote_url, branch, worktree, credentials=credentials
        )
        if exit_code != 0:
            return ("error", stderr.strip()[-400:] or f"git clone exit={exit_code}")
        return ("cloned", None)

    async def _prepare_via_worktree(
        self,
        *,
        worktree: str,
        branch: str,
        git_remote_url: str,
        credentials: dict[str, str],
        project_slug: str | None,
    ) -> tuple[str, str | None]:
        """Bare + `git worktree add` path. Used by production; tests
        can also opt in by leaving `clone_runner` unset and patching
        `worktree_mod.ensure_bare` / `add_worktree`.

        `project_slug` selects the subdirectory under
        `self._bare_root` for this project's bare repo. We tolerate
        None by falling back to the worktree-parent basename — that's
        the shape default-created worktrees use (`/workspace/<slug>/
        <wid>`) so the fallback is correct for the normal path. Tests
        that supply a custom `worktree_path` should also pass slug
        explicitly to avoid relying on the fallback.
        """
        slug = project_slug or os.path.basename(
            os.path.dirname(worktree.rstrip("/")) or "/"
        )
        logger.info(
            "prepare.via_worktree.start",
            slug=slug,
            bare_root=self._bare_root,
            git_remote_url=git_remote_url[:80],
            branch=branch,
            worktree=worktree,
        )
        # Defensive: refuse to call ensure_bare with an empty slug — the
        # bare would land directly under bare_root and collide with
        # every other project that hits the same default. Never happens
        # on the production code path; protects test misuse.
        if not slug:
            return ("error", "project_slug missing — refusing to ensure bare")
        github_token = credentials.get("github_token")
        extra_config: list[str] | None = None
        if github_token:
            extra_config = [
                "-c",
                f"http.extraHeader={_github_basic_header(github_token)}",
            ]
        bare = await worktree_mod.ensure_bare(
            bare_root=self._bare_root,
            project_slug=slug,
            git_remote_url=git_remote_url,
            extra_config=extra_config,
        )
        if not bare.ok:
            msg = bare.stderr.strip()[-400:] or f"bare init exit={bare.exit_code}"
            logger.warning(
                "prepare.via_worktree.ensure_bare.failed",
                slug=slug,
                exit_code=bare.exit_code,
                stderr=msg,
            )
            return ("error", msg)
        logger.info("prepare.via_worktree.ensure_bare.ok", slug=slug)
        add = await worktree_mod.add_worktree(
            bare_root=self._bare_root,
            project_slug=slug,
            worktree_path=worktree,
            branch=branch,
        )
        if not add.ok:
            msg = add.stderr.strip()[-400:] or f"worktree add exit={add.exit_code}"
            logger.warning(
                "prepare.via_worktree.add_worktree.failed",
                slug=slug,
                branch=branch,
                exit_code=add.exit_code,
                stderr=msg,
            )
            return ("error", msg)
        logger.info("prepare.via_worktree.ok", slug=slug, branch=branch)
        return ("cloned", None)

    async def _prepare_worktree_multi(
        self,
        *,
        worktree: str,
        project_slug: str,
        repos: list[RepoSpec],
        credentials: dict[str, str],
    ) -> tuple[str, str | None]:
        """Multi-repo aware version of `_prepare_worktree`.

        Phase N.4 — fans clone work out across the project's
        repositories. Three shapes:

        1. **Empty** (``repos`` is empty): mkdir the worktree dir,
           don't clone anything. Workspaces for empty projects come
           up with a blank canvas ready for ``git init`` or loose
           files. Returns ``("empty", None)``.

        2. **Legacy single-repo** (one repo, ``subpath=""``):
           preserved exactly as pre-N.4 to keep existing workspaces'
           bare-resolution working. Returns whatever
           ``_prepare_via_worktree`` returns.

        3. **Multi-repo or explicit subpath**: each repo is cloned
           into ``<worktree>/<subpath>/`` via its own bare under
           ``<bare_root>/<project_slug>/<repo_id>/``. Repos with NULL
           git_remote_url just get an empty subdir (the "uninitialized
           subdir" placeholder for git-init-later).

        Returns ``(outcome, detail)`` where outcome is "cloned" on
        full success, "partial" when some repos failed (detail
        carries the slug list), "empty" for the no-repo case,
        "error" if the whole orchestration crashed.
        """
        # Empty project → mkdir + return. No bare, no clone.
        if not repos:
            try:
                await asyncio.to_thread(
                    lambda: __import__("pathlib").Path(worktree).mkdir(
                        parents=True, exist_ok=True,
                    )
                )
            except Exception as exc:
                return ("error", f"mkdir worktree failed: {exc!s}"[:400])
            return ("empty", None)

        # Single repo + root subpath = legacy layout. We keep the
        # old code path here so pre-N.4 workspaces keep resolving
        # against their existing bare unchanged.
        if (
            len(repos) == 1
            and repos[0].subpath == _LEGACY_ROOT_SUBPATH
            and repos[0].git_remote_url
        ):
            return await self._prepare_via_worktree(
                worktree=worktree,
                branch=repos[0].branch,
                git_remote_url=repos[0].git_remote_url,
                credentials=credentials,
                project_slug=project_slug,
            )

        # New layout: each repo into its own subdir, each backed by
        # its own bare nested under <project_slug>/<repo_id>.
        # Resume case: an earlier crashed create may have left
        # PARTIAL state. We do NOT short-circuit on a non-empty root
        # worktree dir (it normally holds .gapt/ + .git markers from
        # previous successful repos). Per-repo we check the SUBDIR.
        try:
            await asyncio.to_thread(
                lambda: __import__("pathlib").Path(worktree).mkdir(
                    parents=True, exist_ok=True,
                )
            )
        except Exception as exc:
            return ("error", f"mkdir worktree failed: {exc!s}"[:400])

        github_token = credentials.get("github_token")
        extra_config: list[str] | None = None
        if github_token:
            extra_config = [
                "-c",
                f"http.extraHeader={_github_basic_header(github_token)}",
            ]

        failed: list[str] = []
        for repo in repos:
            sub = _worktree_subdir_for(worktree, repo)
            if not repo.git_remote_url:
                # Empty placeholder — just make the dir.
                try:
                    await asyncio.to_thread(
                        lambda p=sub: __import__("pathlib").Path(p).mkdir(
                            parents=True, exist_ok=True,
                        )
                    )
                except Exception as exc:
                    failed.append(f"{repo.subpath or '(root)'}: mkdir {exc!s}"[:200])
                continue

            # Resume per-repo: if the subdir already has a `.git`,
            # skip — earlier successful create left it.
            already_cloned = await asyncio.to_thread(
                lambda p=sub: os.path.exists(os.path.join(p, ".git"))
            )
            if already_cloned:
                continue

            slug = _bare_slug_for(project_slug, repo, legacy_layout=False)
            bare = await worktree_mod.ensure_bare(
                bare_root=self._bare_root,
                project_slug=slug,
                git_remote_url=repo.git_remote_url,
                extra_config=extra_config,
            )
            if not bare.ok:
                failed.append(
                    f"{repo.subpath or '(root)'}: bare "
                    + (bare.stderr.strip()[-160:] or f"exit={bare.exit_code}")
                )
                continue

            add = await worktree_mod.add_worktree(
                bare_root=self._bare_root,
                project_slug=slug,
                worktree_path=sub,
                branch=repo.branch,
            )
            if not add.ok:
                failed.append(
                    f"{repo.subpath or '(root)'}: worktree "
                    + (add.stderr.strip()[-160:] or f"exit={add.exit_code}")
                )

        if failed:
            return ("partial", " | ".join(failed)[:400])
        return ("cloned", None)

    async def _safe_prepare_worktree_multi(
        self,
        *,
        worktree: str,
        project_slug: str,
        repos: list[RepoSpec],
        credentials: dict[str, str] | None = None,
    ) -> tuple[str, str | None]:
        """Crash-safe wrapper around ``_prepare_worktree_multi`` —
        any unexpected exception is captured and reported as the
        ``("error", ...)`` outcome rather than killing the
        background clone task and leaving the workspace stuck in
        CREATING forever."""
        try:
            return await self._prepare_worktree_multi(
                worktree=worktree,
                project_slug=project_slug,
                repos=repos,
                credentials=credentials or {},
            )
        except Exception as exc:
            logger.exception("workspace.clone_multi.crashed", worktree=worktree)
            return ("error", f"{type(exc).__name__}: {exc}"[:500])

    async def _do_clone(
        self,
        git_remote_url: str,
        branch: str,
        dest_dir: str,
        *,
        credentials: dict[str, str],
    ) -> tuple[int, str, str]:
        """Route to the credential-aware default runner when we both
        have a token *and* haven't been overridden with a custom
        runner. Tests that inject `clone_runner=...` keep their
        existing 3-arg contract untouched."""
        github_token = credentials.get("github_token")
        if self._clone_is_default and github_token:
            return await _default_clone_runner_with_creds(
                git_remote_url, branch, dest_dir, github_token=github_token
            )
        return await self._clone(git_remote_url, branch, dest_dir)

    async def _resolve_credentials(
        self, *, actor_id: str, project_id: str
    ) -> dict[str, str]:
        if self._credentials_resolver is None:
            return {}
        try:
            return await self._credentials_resolver(actor_id, project_id)
        except Exception as exc:  # never block create on a resolver bug
            logger.warning(
                "workspace.credentials.resolve_failed",
                actor_id=actor_id,
                project_id=project_id,
                error=str(exc),
            )
            return {}

    async def _boot_sandbox(
        self,
        *,
        project_id: str,
        workspace_id: str,
        credentials: dict[str, str] | None = None,
    ) -> SandboxRef:
        env = _credentials_to_env(credentials or {})
        spec = SandboxCreateSpec(
            project_id=project_id,
            workspace_id=workspace_id,
            image=self._image,
            resources=SandboxResources(),
            env=env,
        )
        ref = await self._sandbox.create(spec)
        await self._sandbox.start(ref)
        return ref

    # ───────────────────────────────────────────────────── read ──

    async def list_all_active(
        self, db: AsyncSession
    ) -> list[WorkspaceView]:
        """Phase C.2.a — every non-archived workspace across every
        project. Powers the "see what I'm working on right now"
        view on the projects index. Single-admin model means no
        project-level filtering is needed."""
        stmt = (
            select(models.Workspace)
            .where(models.Workspace.status != enums.WorkspaceStatus.ARCHIVED)
            .order_by(models.Workspace.last_activity_at.desc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [_view(r) for r in rows]

    async def active_stats(self, db: AsyncSession) -> tuple[int, int | None]:
        """Phase C.2.d helper. Returns `(active_count, cap)` so the UI
        can surface "5 of 6 workspaces active" — and warn when the
        cap is close. `cap=None` means no cap is configured."""
        from sqlalchemy import func as _sql_func  # noqa: PLC0415

        stmt = select(_sql_func.count()).where(
            models.Workspace.status.in_(
                [
                    enums.WorkspaceStatus.CREATING,
                    enums.WorkspaceStatus.RUNNING,
                ]
            )
        )
        count = (await db.execute(stmt)).scalar_one()
        return count, self._max_active

    async def list_for_project(
        self,
        db: AsyncSession,
        *,
        actor: AdminPrincipal,
        project_id: str,
        include_archived: bool = False,
    ) -> list[WorkspaceView]:
        await fetch_project_for(db, actor=actor, project_id=project_id)
        stmt = (
            select(models.Workspace)
            .where(models.Workspace.project_id == project_id)
            .order_by(models.Workspace.created_at.desc())
        )
        if not include_archived:
            # Archived workspaces are soft-deleted: hide from the
            # default list. The audit log still has them. Callers can
            # opt in with ?include_archived=true if they ever need to
            # restore-or-purge a row.
            stmt = stmt.where(models.Workspace.status != enums.WorkspaceStatus.ARCHIVED)
        rows = (await db.execute(stmt)).scalars().all()
        # Phase N.5 — populate each view's selections so the UI can
        # render per-repo branch chips on the workspace card without
        # an extra round-trip.
        views: list[WorkspaceView] = []
        for r in rows:
            sel = await _selection_views_for(db, workspace_id=r.id)
            views.append(_view(r, sel))
        return views

    async def get(
        self, db: AsyncSession, *, actor: AdminPrincipal, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(db, actor=actor, workspace_id=workspace_id)
        sel = await _selection_views_for(db, workspace_id=row.id)
        return _view(row, sel)

    # ──────────────────────────────────────────────────── mutate ──

    async def stop(
        self, db: AsyncSession, *, actor: AdminPrincipal, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id
        )
        # Swallow "unknown sandbox" — the workspace row outlives the
        # in-memory mock sandbox across server restarts. Stopping a
        # workspace whose sandbox has been swept away is still a
        # legitimate "now stopped" transition from the user's view.
        await self._sandbox_for(db, row, action="stop", swallow_missing=True)
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
        sel = await _selection_views_for(db, workspace_id=row.id)
        return _view(row, sel)

    async def start(
        self, db: AsyncSession, *, actor: AdminPrincipal, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id
        )
        # Same idempotency as stop — if the sandbox was swept, the
        # start request becomes a no-op + status flip. The next time
        # the user does real work the workspace boot path will
        # recreate the sandbox.
        await self._sandbox_for(db, row, action="start", swallow_missing=True)
        row.status = enums.WorkspaceStatus.RUNNING
        row.last_activity_at = _now()
        await db.flush()
        sel = await _selection_views_for(db, workspace_id=row.id)
        return _view(row, sel)

    async def delete(
        self, db: AsyncSession, *, actor: AdminPrincipal, workspace_id: str
    ) -> WorkspaceView:
        row = await self._fetch(
            db, actor=actor, workspace_id=workspace_id
        )
        worktree_path = row.worktree_path
        await self._sandbox_for(db, row, action="destroy", swallow_missing=True)
        row.status = enums.WorkspaceStatus.ARCHIVED
        row.last_activity_at = _now()
        await db.flush()
        # Phase C.1: remove the on-disk worktree (or legacy clone dir).
        # Best-effort — the DB row is already archived, so a transient
        # FS failure shouldn't block the user from rebuilding for the
        # same branch. Skipped entirely on the test path because the
        # injected `clone_runner` stub doesn't materialise real dirs.
        if self._clone_is_default and worktree_path:
            # `remove_worktree` needs the project slug to locate the
            # bare. Fetching the project row is cheap (PK lookup) and
            # we already have the project_id from the workspace row.
            proj_slug: str | None = None
            try:
                proj = (
                    await db.execute(
                        select(models.Project).where(
                            models.Project.id == row.project_id
                        )
                    )
                ).scalar_one_or_none()
                if proj is not None:
                    proj_slug = proj.slug
            except Exception:  # noqa: BLE001 — best-effort
                proj_slug = None
            if proj_slug is None:
                # Fall back to parent-basename heuristic — same shape the
                # default `create()` writes.
                proj_slug = os.path.basename(
                    os.path.dirname(worktree_path.rstrip("/")) or ""
                )
            try:
                await worktree_mod.remove_worktree(
                    bare_root=self._bare_root,
                    project_slug=proj_slug,
                    worktree_path=worktree_path,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning(
                    "workspace.worktree.cleanup_failed",
                    workspace_id=workspace_id,
                    worktree=worktree_path,
                    error=str(exc),
                )
        await self._audit.log(
            AuditEvent(
                action=AuditAction.WORKSPACE_DELETE,
                actor_type=enums.AuditActorType.USER,
                actor_id=actor.id,
                outcome=enums.AuditOutcome.OK,
                scope={"project_id": row.project_id, "workspace_id": workspace_id},
            )
        )
        sel = await _selection_views_for(db, workspace_id=row.id)
        return _view(row, sel)

    # ──────────────────────────────────────────────── internals ──

    async def _sandbox_for(
        self,
        db: AsyncSession,
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
        # Real-docker backends (SysboxBackend) need the actual
        # `container_id`, not None. Mock backend ignores it. Fetching
        # the sandbox row here keeps callers from having to do it.
        sandbox_row = (
            await db.execute(
                select(models.Sandbox).where(models.Sandbox.id == row.sandbox_id)
            )
        ).scalar_one_or_none()
        container_id = sandbox_row.container_id if sandbox_row else None
        ref = SandboxRef(
            id=row.sandbox_id, container_id=container_id, backend=self._sandbox.name
        )
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
        actor: AdminPrincipal,
        workspace_id: str,
    ) -> models.Workspace:
        row = (
            await db.execute(select(models.Workspace).where(models.Workspace.id == workspace_id))
        ).scalar_one_or_none()
        if row is None:
            raise WorkspaceError("workspace.not_found", f"workspace_id={workspace_id}")

        # Reuse the project authorisation gate.
        await fetch_project_for(db, actor=actor, project_id=row.project_id)
        return row


def _now() -> datetime:
    return datetime.now(tz=UTC)


# Map secret `key_name` → sandbox env var(s). Includes aliases so common
# CLIs (gh, openai, anthropic) and SDKs find the value without callers
# having to remember exact env names.
_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "github_token": ("GITHUB_TOKEN", "GH_TOKEN"),
    "openai_api_key": ("OPENAI_API_KEY",),
    "anthropic_api_key": ("ANTHROPIC_API_KEY",),
}


def _credentials_to_env(credentials: dict[str, str]) -> dict[str, str]:
    """Materialise resolved credentials as sandbox environment vars.

    Unknown key_names fall back to `KEY_NAME.upper()` so future tokens
    show up in the sandbox without code changes (still scoped to the
    user-secrets the Settings UI surfaces)."""
    env: dict[str, str] = {}
    for key, value in credentials.items():
        for alias in _ENV_ALIASES.get(key, (key.upper(),)):
            env[alias] = value
    return env
