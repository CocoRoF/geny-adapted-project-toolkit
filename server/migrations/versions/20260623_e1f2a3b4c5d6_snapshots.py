"""snapshots — git-grade workspace checkpoints (files + artifacts + agent activity)

Revision ID: e1f2a3b4c5d6
Revises: c1d2e3f4a5b6
Create Date: 2026-06-23 00:00:00.000000

P1 of Sandbox Tool Packs. A snapshot pins a commit on ``refs/snapshots/<id>``
(file state + force-included build artifacts) plus the agent activity that
produced it (chat + tool calls, via the session_events seq range + a compact
transcript), chained into a DAG by ``parent_id``.

Additive — new table + new enum type. Downgrade drops both.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("workspace_id", sa.String(length=26), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=True),
        sa.Column("parent_id", sa.String(length=26), nullable=True),
        sa.Column(
            "kind",
            sa.Enum("manual", "tool_save", "auto", name="snapshot_kind_enum"),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("label", sa.String(length=255), server_default="", nullable=False),
        sa.Column("git_ref", sa.String(length=255), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=False),
        sa.Column("event_start_seq", sa.Integer(), nullable=True),
        sa.Column("event_end_seq", sa.Integer(), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "activity",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_snapshots_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name=op.f("fk_snapshots_session_id_agent_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["snapshots.id"],
            name=op.f("fk_snapshots_parent_id_snapshots"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_snapshots")),
    )
    op.create_index(
        "ix_snapshots_workspace_created", "snapshots", ["workspace_id", "created_at"]
    )
    op.create_index("ix_snapshots_session_id", "snapshots", ["session_id"])
    op.create_index("ix_snapshots_parent_id", "snapshots", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_snapshots_parent_id", table_name="snapshots")
    op.drop_index("ix_snapshots_session_id", table_name="snapshots")
    op.drop_index("ix_snapshots_workspace_created", table_name="snapshots")
    op.drop_table("snapshots")
    sa.Enum(name="snapshot_kind_enum").drop(op.get_bind(), checkfirst=True)
