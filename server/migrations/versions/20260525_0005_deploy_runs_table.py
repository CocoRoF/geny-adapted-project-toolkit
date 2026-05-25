"""deploy_runs: per-deploy audit-grade table.

Replaces the JSONB-only `environments.last_run` snapshot as the
source of truth for deploy history. `last_run` stays as a
denormalised cache for the UI's "current state" header; full
history + rollback choices read from `deploy_runs`.

Revision ID: 0005_deploy_runs_table
Revises: 0004_environments_last_run
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_deploy_runs_table"
down_revision: str | Sequence[str] | None = "0004_environments_last_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deploy_runs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "environment_id",
            sa.String(26),
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(200), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("bound_url", sa.Text(), nullable=True),
        sa.Column("exec_code", sa.String(80), nullable=True),
        sa.Column("log_tail", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "actor_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "trigger_kind",
            sa.String(40),
            nullable=False,
            server_default="manual",
        ),
    )
    op.create_index(
        "ix_deploy_runs_environment_id", "deploy_runs", ["environment_id"]
    )
    op.create_index("ix_deploy_runs_started_at", "deploy_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_deploy_runs_started_at", table_name="deploy_runs")
    op.drop_index("ix_deploy_runs_environment_id", table_name="deploy_runs")
    op.drop_table("deploy_runs")
