"""ProjectRepository service — CRUD for the per-project git repos.

Phase N.4 — Project is a logical container of zero or more
``ProjectRepository`` rows. Each row carries the bundle of fields
that used to live on the Project itself (git_remote_url + provider +
auth + compose paths) so a workspace can clone N independent repos
into VS-Code-style subdirs under one worktree.

The service is intentionally thin — it's a typed wrapper over a few
SQLAlchemy queries. The migration that backs this layer auto-creates
one row per existing Project at ``subpath=''`` so every legacy
project keeps behaving exactly as it did pre-N.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gapt_server.db import enums, models
from gapt_server.db.ulid import new_ulid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RepositoryError(RuntimeError):
    """Domain error — carries a stable code suffix for the API layer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RepositoryCreate:
    """Inputs the operator (or scaffold flow) supplies when adding a
    repo to a project. Most fields mirror the old Project columns. A
    NULL ``git_remote_url`` is reserved for the "create an empty
    subdir, init git later" flow; not exposed in the UI yet but the
    domain layer accepts it so the cutover can land without another
    migration."""

    subpath: str
    display_name: str
    git_remote_url: str | None = None
    git_provider: enums.GitProvider | None = None
    git_auth_secret_ref: str | None = None
    default_compose_paths: tuple[str, ...] = ()
    compose_profile_dev: str | None = None
    compose_profile_prod: str | None = None
    default_branch: str | None = None
    sort_order: int = 0


def _validate_subpath(subpath: str) -> None:
    """Subpath must be either '' (legacy project-root layout) or a
    single path segment with no slashes or leading dots. The worktree
    folder name is derived from it verbatim — preventing slashes
    keeps the on-disk layout flat and the clone path predictable."""
    if subpath == "":
        return
    if "/" in subpath or "\\" in subpath:
        raise RepositoryError(
            "repository.subpath_invalid",
            f"subpath must be a single segment, not a path: {subpath!r}",
        )
    if subpath.startswith(".") or subpath in {"..", "."}:
        raise RepositoryError(
            "repository.subpath_invalid",
            f"subpath cannot start with '.' or be a relative marker: {subpath!r}",
        )


async def list_for_project(
    db: AsyncSession, *, project_id: str, include_archived: bool = False
) -> list[models.ProjectRepository]:
    """Return the project's repos in sort_order ascending. Archived
    rows are filtered out by default; the audit / detail surface can
    opt back in by passing ``include_archived=True``."""
    stmt = (
        select(models.ProjectRepository)
        .where(models.ProjectRepository.project_id == project_id)
        .order_by(
            models.ProjectRepository.sort_order.asc(),
            models.ProjectRepository.created_at.asc(),
        )
    )
    if not include_archived:
        stmt = stmt.where(models.ProjectRepository.archived_at.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def primary_for_project(
    db: AsyncSession, *, project_id: str
) -> models.ProjectRepository | None:
    """The first active repository by sort_order. Used as the
    implicit target for back-compat code paths that historically
    relied on Project.git_remote_url. Returns None for empty
    projects."""
    rows = await list_for_project(db, project_id=project_id)
    return rows[0] if rows else None


async def get(db: AsyncSession, *, repository_id: str) -> models.ProjectRepository | None:
    return await db.get(models.ProjectRepository, repository_id)


async def add(
    db: AsyncSession, *, project_id: str, payload: RepositoryCreate
) -> models.ProjectRepository:
    """Insert a new repository row. Raises ``RepositoryError`` with
    a stable code on subpath validation / uniqueness conflict —
    callers map those to HTTP 400 / 409.

    Does NOT clone the repo on disk — that's the workspace creation
    path's job, and only runs when a workspace is actually started
    (so adding a repo to a project with no workspaces is a pure
    metadata op).
    """
    _validate_subpath(payload.subpath)
    row = models.ProjectRepository(
        id=new_ulid(),
        project_id=project_id,
        subpath=payload.subpath,
        display_name=payload.display_name,
        git_remote_url=payload.git_remote_url,
        git_provider=payload.git_provider,
        git_auth_secret_ref=payload.git_auth_secret_ref,
        default_compose_paths=list(payload.default_compose_paths),
        compose_profile_dev=payload.compose_profile_dev,
        compose_profile_prod=payload.compose_profile_prod,
        default_branch=payload.default_branch,
        sort_order=payload.sort_order,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise RepositoryError(
            "repository.subpath_conflict",
            f"subpath {payload.subpath!r} already in use under this project",
        ) from exc
    return row


async def archive(db: AsyncSession, *, repository_id: str) -> models.ProjectRepository:
    """Soft-delete by stamping ``archived_at``. The on-disk worktree
    folder is left alone — the operator can remove it manually if
    they want the space back. Next workspace creation will skip the
    archived row.

    Soft-delete must reproduce what the FK ``ondelete=SET NULL`` would
    do on a hard delete, otherwise dependent rows keep pointing at a
    now-invisible repo: a ``WorkspaceRepository`` selection would show
    stale data, and an ``Environment`` would target a repo that no
    longer participates. We NULL both so they fall back to their
    documented defaults (selection → "(archived)" placeholder handled
    by the read path; env → project-wide primary)."""
    from datetime import UTC, datetime  # noqa: PLC0415 — local import to avoid cycle

    from sqlalchemy import update  # noqa: PLC0415

    row = await db.get(models.ProjectRepository, repository_id)
    if row is None:
        raise RepositoryError(
            "repository.not_found",
            f"repository {repository_id!r} does not exist",
        )
    if row.archived_at is not None:
        return row
    row.archived_at = datetime.now(UTC)
    await db.execute(
        update(models.WorkspaceRepository)
        .where(models.WorkspaceRepository.project_repository_id == repository_id)
        .values(project_repository_id=None)
    )
    await db.execute(
        update(models.Environment)
        .where(models.Environment.repository_id == repository_id)
        .values(repository_id=None)
    )
    await db.flush()
    return row
