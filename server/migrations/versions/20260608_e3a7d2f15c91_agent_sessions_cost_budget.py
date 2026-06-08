"""agent_sessions.cost_budget_usd

Revision ID: e3a7d2f15c91
Revises: b1e2c3d4f7a9
Create Date: 2026-06-08 16:00:00.000000

Phase N.3 — GAPT-side budget enforcement.

Previously the per-session ``cost_budget_usd`` came in via
``CreateSessionRequest`` and was baked into the manifest's
``pipeline.cost_budget_usd`` slot, consumed only by geny-executor's
``--max-budget-usd`` CLI flag. That made the spawned ``claude``
subprocess budget-aware and leaked "남은 예산이 빠듯하니" meta-cognitive
chatter into the agent's responses.

This migration moves the budget into GAPT's own ownership:

* New column persists the per-session cap so a server restart's
  rehydrate path still knows the limit.
* The invoke handler checks ``cost_usd >= cost_budget_usd`` before
  spawning the executor turn → returns ``session.budget_exhausted``
  with the current totals so the chat UI can surface a clean
  "한도 초과" banner.
* The executor stops receiving ``--max-budget-usd`` entirely
  (default flipped to ``None`` in ``credentials.py``).

Nullable → ``NULL`` means "no cap" (free-mode, opt-in). Existing
rows default to NULL, preserving prior behaviour for sessions
created before this migration ran.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a7d2f15c91"
down_revision: str | Sequence[str] | None = "b1e2c3d4f7a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("cost_budget_usd", sa.Numeric(10, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "cost_budget_usd")
