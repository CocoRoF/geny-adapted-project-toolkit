"""admin_agent_prefs.default_manifest_id

Revision ID: c7d2e9a3f410
Revises: b8e1c4af2210
Create Date: 2026-05-28 16:30:00.000000

Phase G.5 — operator picks a workspace-wide default manifest
without redeploying. Resolved by `ProjectAwareSessionManager.
create_session` when the request body doesn't supply `env_id`.
Null falls back to `Settings.default_manifest_id` (gapt_default).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c7d2e9a3f410"
down_revision: str | Sequence[str] | None = "b8e1c4af2210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_agent_prefs",
        sa.Column("default_manifest_id", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_agent_prefs", "default_manifest_id")
