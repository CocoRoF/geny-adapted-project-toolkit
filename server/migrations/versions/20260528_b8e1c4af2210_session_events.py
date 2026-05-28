"""session_events — Phase D.3 durable SSE event log

Revision ID: b8e1c4af2210
Revises: a4c3b2d9e7f8
Create Date: 2026-05-28 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b8e1c4af2210'
down_revision: str | Sequence[str] | None = 'a4c3b2d9e7f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'session_events',
        sa.Column('session_id', sa.String(length=26), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column(
            'data',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='{}',
            nullable=False,
        ),
        sa.Column(
            'ts',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['session_id'],
            ['agent_sessions.id'],
            ondelete='CASCADE',
            name=op.f('fk_session_events_session_id_agent_sessions'),
        ),
        sa.PrimaryKeyConstraint('session_id', 'seq', name=op.f('pk_session_events')),
    )
    op.create_index(
        'ix_session_events_session_seq',
        'session_events',
        ['session_id', 'seq'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_session_events_session_seq', table_name='session_events')
    op.drop_table('session_events')
