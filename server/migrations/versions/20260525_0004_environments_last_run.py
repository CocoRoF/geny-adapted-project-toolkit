"""environments: add last_run JSONB column.

Stores `{run_id, status, bound_url, deployed_at, version}` for the
most recent deploy attempt. Lets the UI show "last deployed X,
current URL Y" without a runs table. Defaults to an empty object
so existing rows don't need backfill.

Revision ID: 0004_environments_last_run
Revises: 0003_agent_prefs_permission_mode
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_environments_last_run"
down_revision: str | Sequence[str] | None = "0003_agent_prefs_permission_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column(
            "last_run",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("environments", "last_run")
