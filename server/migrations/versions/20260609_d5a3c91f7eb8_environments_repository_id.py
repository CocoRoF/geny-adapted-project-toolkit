"""environments.repository_id — Phase N.4

Revision ID: d5a3c91f7eb8
Revises: f7b8e3d2a165
Create Date: 2026-06-09 13:00:00.000000

Each Environment now optionally targets a specific
``project_repositories`` row, so multi-repo projects can deploy each
repo as its own stack (independent compose paths, Caddy slugs, etc.).
NULL = "use the project-wide default compose paths" (legacy
behaviour). Auto-migrates existing envs to the project's primary
repo so prod stacks keep deploying the same code as before.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a3c91f7eb8"
down_revision: str | Sequence[str] | None = "f7b8e3d2a165"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column(
            "repository_id",
            sa.String(length=26),
            sa.ForeignKey("project_repositories.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Backfill: each env points at its project's primary repository.
    # primary = lowest sort_order, oldest created_at on tie. Done with
    # a single update from a window function — no Python loop.
    op.execute(sa.text("""
        WITH primaries AS (
            SELECT DISTINCT ON (project_id)
                project_id, id
            FROM project_repositories
            WHERE archived_at IS NULL
            ORDER BY project_id, sort_order ASC, created_at ASC
        )
        UPDATE environments e
        SET repository_id = p.id
        FROM primaries p
        WHERE e.project_id = p.project_id
          AND e.repository_id IS NULL;
    """))


def downgrade() -> None:
    op.drop_column("environments", "repository_id")
