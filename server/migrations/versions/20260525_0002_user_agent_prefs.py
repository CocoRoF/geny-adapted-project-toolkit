"""user_agent_prefs — user-global Agent / manifest overrides.

Adds a single table that the Settings UI writes into. Every column
is nullable so partial overrides (e.g. model only) work cleanly.
The DB column `cost_budget_usd` is `Numeric(10,4)` to avoid float
drift on what is, after all, a dollar amount.

Revision ID: 0002_user_agent_prefs
Revises: 0001_init
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_agent_prefs"
down_revision: str | Sequence[str] | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_agent_prefs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("max_iterations", sa.Integer(), nullable=True),
        sa.Column("cost_budget_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("timeout_s", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_agent_prefs")
