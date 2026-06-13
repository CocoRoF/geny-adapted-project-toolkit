"""Add btree indexes on FK columns used by ON DELETE cascade.

Without these, purging a project (or workspace) makes Postgres
sequentially scan each child table to find the rows to cascade-delete
— fine at hobby scale, O(n) per delete as history grows. These six
columns are all FK targets of a cascade / SET NULL relationship but
were never indexed.

Revision ID: c1d2e3f4a5b6
Revises: a8c4b2e9d731
Create Date: 2026-06-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "a8c4b2e9d731"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (index_name, table, column) — kept in one list so up/down stay in sync.
_INDEXES: list[tuple[str, str, str]] = [
    ("ix_environments_repository_id", "environments", "repository_id"),
    ("ix_workspaces_sandbox_id", "workspaces", "sandbox_id"),
    ("ix_sandboxes_project_id", "sandboxes", "project_id"),
    ("ix_sandboxes_workspace_id", "sandboxes", "workspace_id"),
    ("ix_agent_sessions_workspace_id", "agent_sessions", "workspace_id"),
    (
        "ix_workspace_repositories_project_repository_id",
        "workspace_repositories",
        "project_repository_id",
    ),
]


def upgrade() -> None:
    for name, table, column in _INDEXES:
        op.create_index(name, table, [column], if_not_exists=True)


def downgrade() -> None:
    for name, table, _column in _INDEXES:
        op.drop_index(name, table_name=table, if_exists=True)
