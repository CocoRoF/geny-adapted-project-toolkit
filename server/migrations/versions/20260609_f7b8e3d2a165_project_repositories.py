"""project_repositories — multi-git support

Revision ID: f7b8e3d2a165
Revises: e3a7d2f15c91
Create Date: 2026-06-09 11:00:00.000000

Phase N.4 — promote git_remote_url + provider + auth + compose paths
from ``projects`` into its own ``project_repositories`` row, so:

* a project can hold zero or more git repositories
* an "empty" project (no repos) is a first-class citizen — workspaces
  get a plain worktree dir, ready for `git init` or loose files
* multi-repo projects clone each repo into its own subpath under the
  workspace's worktree (VS Code multi-root layout)
* per-repo auth lets OSS + private repos coexist in one project
* per-repo compose paths let each repo deploy as its own stack

Data migration: for every existing project, one row is inserted with
``subpath=''`` (legacy project-root layout) carrying its git +
compose bundle. The ``projects`` columns themselves stay in place for
this migration — a follow-up will drop them once all callers have
been moved to read from project_repositories.

Downgrade: drop the table. Legacy projects keep working off the
unchanged ``projects`` columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7b8e3d2a165"
down_revision: str | Sequence[str] | None = "e3a7d2f15c91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The enum already exists from the original projects table
    # migration. ``create_type=False`` tells alembic NOT to
    # re-CREATE it (would 42710 "type already exists"); the column
    # still binds to the existing pg type by name.
    git_provider_enum = postgresql.ENUM(
        "github", "gitlab", "bitbucket", "other",
        name="git_provider_enum",
        create_type=False,
    )

    op.create_table(
        "project_repositories",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=26),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subpath",
            sa.String(length=120),
            nullable=False,
            server_default="",
        ),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("git_remote_url", sa.Text(), nullable=True),
        sa.Column("git_provider", git_provider_enum, nullable=True),
        sa.Column("git_auth_secret_ref", sa.Text(), nullable=True),
        sa.Column(
            "default_compose_paths",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("compose_profile_dev", sa.String(length=80), nullable=True),
        sa.Column("compose_profile_prod", sa.String(length=80), nullable=True),
        sa.Column("default_branch", sa.String(length=255), nullable=True),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "project_id", "subpath",
            name="uq_project_repositories_subpath",
        ),
    )
    op.create_index(
        "ix_project_repositories_project_sort",
        "project_repositories",
        ["project_id", "sort_order"],
    )

    # Data migration — one row per existing project.
    #
    # We synthesize a ULID-ish id with `to_char(now() ...)` won't
    # cut it — they'd collide across rows. Use pgcrypto's
    # gen_random_uuid() then strip dashes + pad/truncate to 26 chars.
    # Not a real ULID (no time prefix), but matches the column length
    # and is unique per row, which is what FK relationships care
    # about. The application never re-parses these as ULIDs.
    op.execute(sa.text("""
        CREATE EXTENSION IF NOT EXISTS pgcrypto;
        INSERT INTO project_repositories (
            id,
            project_id,
            subpath,
            display_name,
            git_remote_url,
            git_provider,
            git_auth_secret_ref,
            default_compose_paths,
            compose_profile_dev,
            compose_profile_prod,
            default_branch,
            sort_order
        )
        SELECT
            UPPER(SUBSTRING(REPLACE(gen_random_uuid()::text, '-', '') FROM 1 FOR 26)),
            p.id,
            '' AS subpath,
            p.display_name,
            p.git_remote_url,
            p.git_provider,
            p.git_auth_secret_ref,
            p.default_compose_paths,
            p.compose_profile_dev,
            p.compose_profile_prod,
            NULL AS default_branch,
            0 AS sort_order
        FROM projects p
        WHERE p.archived_at IS NULL
        ON CONFLICT (project_id, subpath) DO NOTHING;
    """))


def downgrade() -> None:
    op.drop_index(
        "ix_project_repositories_project_sort",
        table_name="project_repositories",
    )
    op.drop_table("project_repositories")
