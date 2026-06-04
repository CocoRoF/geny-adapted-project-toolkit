"""Phase N.2.5 — projects.scaffold_preset_id audit column.

Records which scaffold preset (or NULL for imported repos) created the
project row. Audit-only; nothing in the request path reads it back
(though future analytics / UX hints could).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1e2c3d4f7a9"
down_revision: str | Sequence[str] | None = "5a9af81931ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("scaffold_preset_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "scaffold_preset_id")
