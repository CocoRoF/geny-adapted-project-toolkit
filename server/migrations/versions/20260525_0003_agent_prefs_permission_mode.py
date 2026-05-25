"""user_agent_prefs: add permission_mode column.

Lets users dial back the CLI's auto-approve behaviour from the
shipped default of `bypassPermissions` to `acceptEdits` / `default` /
`plan`. Nullable — null means "use the server default
(`bypassPermissions`)".

Revision ID: 0003_agent_prefs_permission_mode
Revises: 0002_user_agent_prefs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_agent_prefs_permission_mode"
down_revision: str | Sequence[str] | None = "0002_user_agent_prefs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_agent_prefs",
        sa.Column("permission_mode", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_agent_prefs", "permission_mode")
