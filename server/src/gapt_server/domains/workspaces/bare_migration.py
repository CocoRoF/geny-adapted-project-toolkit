"""One-shot migration: move legacy `<project_root>/.bare/` repos to
the new `<workspace_bare_root>/<project_slug>/` layout.

Why this exists: until 2026-05-29 the bare lived as a sibling of
workspace dirs inside each project root. When the workspace container
was mounted at `/workspace`, mounting the bare at its absolute host
path (`/workspace/<slug>/.bare`) made the bare's parent dir show up
as an untracked entry inside the worktree (e.g. `test/` polluting
`git status`). Moving the bare *out* of `workspace_root` fixes that
permanently, but every project that already has a bare needs to be
migrated.

The migration runs once at server start and is idempotent — projects
already on the new layout are skipped without touching anything.

Migration steps per project:
  1. Detect old bare at `<workspace_root>/<slug>/.bare/`.
  2. `mv` it to `<bare_root>/<slug>/` (cross-filesystem-safe via
     `shutil.move`).
  3. For each worktree under it, rewrite the worktree's `.git` file
     so `gitdir:` points at the new bare path.
  4. Rewrite each `<bare>/worktrees/<wid>/gitdir` to point at the
     worktree's `.git` (path didn't change, but git's lock detection
     reads this file so we keep it canonical).

Refuses to run when the new bare path already exists for a project —
that means someone migrated already (or a stale dir from a previous
attempt sits there). The operator decides whether to clean up.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

import structlog

from gapt_server.db import models
from gapt_server.domains.workspaces import worktree as worktree_mod

logger = structlog.get_logger(__name__)


@dataclass
class MigrationReport:
    """What changed. Logged at info level after the migration sweep."""

    migrated: list[str] = field(default_factory=list)  # slugs
    skipped_no_legacy: list[str] = field(default_factory=list)
    skipped_target_exists: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (slug, reason)


def _legacy_bare_for(workspace_root: str, project_slug: str) -> str:
    return os.path.join(workspace_root, project_slug, ".bare")


def _scan_workspaces_in_project_root(project_root: str) -> list[str]:
    """Every immediate subdir of `<project_root>` whose `.git` is a
    file (worktree layout). Returns the absolute worktree paths."""
    out: list[str] = []
    if not os.path.isdir(project_root):
        return out
    try:
        for name in os.listdir(project_root):
            if name == ".bare":
                continue
            sub = os.path.join(project_root, name)
            if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, ".git")):
                out.append(sub)
    except OSError:
        return out
    return out


def migrate_project_bare(
    *,
    workspace_root: str,
    workspace_bare_root: str,
    project_slug: str,
) -> str:
    """Migrate one project. Returns a verdict string for the report:
    `"migrated"`, `"no_legacy"`, `"target_exists"`. Raises on actual
    failure so the caller can record it in `MigrationReport.failed`."""
    legacy_bare = _legacy_bare_for(workspace_root, project_slug)
    new_bare = worktree_mod.bare_dir(workspace_bare_root, project_slug)

    if not os.path.isdir(legacy_bare):
        return "no_legacy"
    if os.path.isdir(new_bare):
        # Someone migrated already (or a stale dir from a previous
        # attempt). Leave both alone — the operator decides.
        return "target_exists"

    os.makedirs(os.path.dirname(new_bare), exist_ok=True)
    shutil.move(legacy_bare, new_bare)

    project_root = os.path.join(workspace_root, project_slug)
    for worktree_path in _scan_workspaces_in_project_root(project_root):
        wid = os.path.basename(worktree_path)
        # Worktree's `.git` file: gitdir: <new_bare>/worktrees/<wid>
        new_gitdir = os.path.join(new_bare, "worktrees", wid)
        worktree_mod.rewrite_worktree_gitdir(worktree_path, new_gitdir)
        # Bare's reverse pointer: still the worktree's `.git` (path
        # unchanged for the worktree itself).
        worktree_mod.rewrite_bare_worktree_pointer(
            new_bare, workspace_id=wid, worktree_path=worktree_path
        )
    return "migrated"


async def run_bare_migration(
    *,
    session_factory,
    workspace_root: str,
    workspace_bare_root: str,
) -> MigrationReport:
    """Scan every non-archived project's slug, attempt migration.
    Refusal cases (`target_exists`) are not failures — they're a
    signal that operator already moved the bare. Real OSErrors are
    captured per-project so one bad project can't block the others.
    """
    from sqlalchemy import select  # noqa: PLC0415

    report = MigrationReport()
    async with session_factory() as db:
        rows = (
            await db.execute(select(models.Project.slug, models.Project.archived_at))
        ).all()
    for slug, archived_at in rows:
        if archived_at is not None:
            # Archived project — don't touch its on-disk state.
            continue
        try:
            verdict = migrate_project_bare(
                workspace_root=workspace_root,
                workspace_bare_root=workspace_bare_root,
                project_slug=slug,
            )
        except Exception as exc:  # noqa: BLE001
            report.failed.append((slug, str(exc)))
            logger.warning(
                "workspace.bare_migration.failed",
                project_slug=slug,
                error=str(exc),
            )
            continue
        if verdict == "migrated":
            report.migrated.append(slug)
            logger.info("workspace.bare_migration.moved", project_slug=slug)
        elif verdict == "target_exists":
            report.skipped_target_exists.append(slug)
            logger.warning(
                "workspace.bare_migration.target_exists",
                project_slug=slug,
                hint="new bare path already populated — manual review",
            )
        else:
            report.skipped_no_legacy.append(slug)
    return report
