"""agent_sessions.cache_write_tokens + cache_read_tokens

Revision ID: 5a9af81931ba
Revises: c7d2e9a3f410
Create Date: 2026-06-01 02:00:00.000000

Phase K.2 — track Anthropic cache tokens explicitly so the cost
dashboard / SessionDetail can explain why a "6 input + 6 output"
turn cost $0.013 (cache_write is the missing dimension).

Additive migration — both columns NOT NULL with server-default 0
so existing rows keep working and the downgrade is a clean
drop_column.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "5a9af81931ba"
down_revision: str | Sequence[str] | None = "c7d2e9a3f410"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column(
            "cache_write_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "agent_sessions",
        sa.Column(
            "cache_read_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "cache_read_tokens")
    op.drop_column("agent_sessions", "cache_write_tokens")
