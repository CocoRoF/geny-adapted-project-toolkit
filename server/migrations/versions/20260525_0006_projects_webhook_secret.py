"""projects: add webhook_secret column.

HMAC secret for inbound GitHub-style push webhooks. Nullable —
each project mints its own via `POST /webhooks/secret`. Stored as
plain string (length 64 = 256-bit hex) because the verification
path needs the plaintext at every webhook call; this isn't a user
credential and rotation is one-click.

Revision ID: 0006_projects_webhook_secret
Revises: 0005_deploy_runs_table
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_projects_webhook_secret"
down_revision: str | Sequence[str] | None = "0005_deploy_runs_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("webhook_secret", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "webhook_secret")
