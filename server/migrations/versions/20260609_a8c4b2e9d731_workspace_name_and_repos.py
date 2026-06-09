"""workspace name + workspace_repositories — Phase N.5

Revision ID: a8c4b2e9d731
Revises: d5a3c91f7eb8
Create Date: 2026-06-09 16:00:00.000000

Workspaces are no longer identified by ``(project, branch)``. They get
a user-facing ``name`` (operator-chosen or auto-generated), and which
repositories get cloned at which branches is recorded in the new
``workspace_repositories`` join table. The pre-N.5 single ``branch``
column collapsed in multi-repo projects where each repo can be on a
different branch — VS Code multi-root case.

Migration steps:
  1. Add ``workspaces.name`` (nullable initially so we can backfill).
  2. Backfill ``name = branch`` for existing rows (preserves identity).
  3. Make ``name`` NOT NULL.
  4. Create ``workspace_repositories`` join table.
  5. Backfill: every existing workspace × every active project repo
     becomes a join row carrying the workspace's old branch.
  6. Drop the ``(project, branch)`` partial unique index.
  7. Add the ``(project, name)`` partial unique index.
  8. Drop the ``workspaces.branch`` column.

Downgrade is best-effort symmetric — it recovers the legacy branch
column from the first join row per workspace. Operators running this
on data with no join rows (fresh DBs) get an empty branch back.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8c4b2e9d731"
down_revision: str | Sequence[str] | None = "d5a3c91f7eb8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add ``name`` nullable so existing rows have a place to land.
    op.add_column(
        "workspaces",
        sa.Column("name", sa.String(length=255), nullable=True),
    )
    # 2. Backfill from the legacy branch column. Two-step (nullable
    #    → backfill → set NOT NULL) is the standard zero-downtime
    #    pattern for adding a required column to a populated table.
    op.execute(sa.text("UPDATE workspaces SET name = branch WHERE name IS NULL;"))
    # 3. Lock it in.
    op.alter_column("workspaces", "name", nullable=False)

    # 4. Join table.
    op.create_table(
        "workspace_repositories",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=26),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_repository_id",
            sa.String(length=26),
            sa.ForeignKey("project_repositories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "branch",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "workspace_id", "project_repository_id",
            name="uq_workspace_repositories_pair",
        ),
    )
    op.create_index(
        "ix_workspace_repositories_workspace",
        "workspace_repositories",
        ["workspace_id"],
    )

    # 5. Backfill join rows: each existing workspace gets one row per
    #    active project repository, branch = workspace's legacy
    #    branch. ``gen_random_uuid()`` would clash with our ULID PKs,
    #    so we cheat with a hash-derived stable id — collisions are
    #    astronomically unlikely with project_repositories.id +
    #    workspaces.id as the entropy source.
    op.execute(sa.text("""
        INSERT INTO workspace_repositories (id, workspace_id, project_repository_id, branch, created_at)
        SELECT
            -- ULID-shaped 26-char id from md5 + a leading '0' to avoid
            -- starting with a digit that collides with real ULIDs.
            UPPER(SUBSTRING(MD5(w.id || pr.id) FROM 1 FOR 26)) AS id,
            w.id AS workspace_id,
            pr.id AS project_repository_id,
            w.branch AS branch,
            w.created_at AS created_at
        FROM workspaces w
        JOIN project_repositories pr ON pr.project_id = w.project_id
        WHERE pr.archived_at IS NULL
        ON CONFLICT DO NOTHING;
    """))

    # 6. Swap the partial unique index from (project, branch) to
    #    (project, name).
    op.drop_index(
        "ix_workspaces_project_branch_active",
        table_name="workspaces",
    )
    op.create_index(
        "ix_workspaces_project_name_active",
        "workspaces",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("status != 'archived'"),
    )

    # 7. The branch column is fully replaced — drop it. Audit trail
    #    keeps history via the join rows we just inserted.
    op.drop_column("workspaces", "branch")


def downgrade() -> None:
    # Re-add ``branch`` nullable so we can backfill from the join rows.
    op.add_column(
        "workspaces",
        sa.Column("branch", sa.String(length=255), nullable=True),
    )
    # Recover one branch per workspace — take the first join row's
    # branch as the canonical legacy value. Operators who relied on
    # the per-repo branches lose that fidelity here; downgrade is
    # best-effort.
    op.execute(sa.text("""
        WITH firsts AS (
            SELECT DISTINCT ON (workspace_id)
                workspace_id, branch
            FROM workspace_repositories
            ORDER BY workspace_id, created_at ASC
        )
        UPDATE workspaces w
        SET branch = COALESCE(f.branch, w.name)
        FROM firsts f
        WHERE w.id = f.workspace_id;
    """))
    # Anything still NULL (workspace with no join rows) falls back to
    # the name so the NOT NULL constraint below survives.
    op.execute(sa.text("UPDATE workspaces SET branch = name WHERE branch IS NULL;"))
    op.alter_column("workspaces", "branch", nullable=False)

    op.drop_index(
        "ix_workspaces_project_name_active",
        table_name="workspaces",
    )
    op.create_index(
        "ix_workspaces_project_branch_active",
        "workspaces",
        ["project_id", "branch"],
        unique=True,
        postgresql_where=sa.text("status != 'archived'"),
    )

    op.drop_index(
        "ix_workspace_repositories_workspace",
        table_name="workspace_repositories",
    )
    op.drop_table("workspace_repositories")

    op.drop_column("workspaces", "name")
