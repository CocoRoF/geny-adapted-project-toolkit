"""workspaces: partial unique (project_id, branch) for non-archived

Revision ID: a4c3b2d9e7f8
Revises: fd2e71745ed6
Create Date: 2026-05-28 11:00:00.000000

Phase C.1 — Worktree-1st workspace model. One *active* workspace per
(project_id, branch). Archived rows are exempt so they can pile up for
audit value, and a project can re-spawn a workspace for the same
branch after the previous one is archived.

Migration safety: an existing deployment may already have duplicate
non-archived rows (the old model allowed N workspaces per branch).
This migration auto-archives older duplicates *before* creating the
partial unique index so the DDL doesn't fail.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4c3b2d9e7f8'
down_revision: str | Sequence[str] | None = 'fd2e71745ed6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Archive older duplicates so the partial unique index is
    #    creatable. Keep the most recently created row per
    #    (project_id, branch); flip everything else to archived.
    op.execute(
        """
        UPDATE workspaces AS w
        SET status = 'archived'
        WHERE w.status != 'archived'
          AND EXISTS (
            SELECT 1 FROM workspaces AS newer
            WHERE newer.project_id = w.project_id
              AND newer.branch = w.branch
              AND newer.status != 'archived'
              AND newer.created_at > w.created_at
          );
        """
    )
    # 2. Create the partial unique index.
    op.create_index(
        'ix_workspaces_project_branch_active',
        'workspaces',
        ['project_id', 'branch'],
        unique=True,
        postgresql_where=sa.text("status != 'archived'"),
    )


def downgrade() -> None:
    op.drop_index(
        'ix_workspaces_project_branch_active',
        table_name='workspaces',
    )
    # No reverse for the archival step — archiving is a non-destructive
    # status flip and reversing would risk re-creating constraint
    # violations.
