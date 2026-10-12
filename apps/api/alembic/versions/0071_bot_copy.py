"""Published bots are locked by default: members copy one only when its publisher allows it.

- `publications.allow_copy` (「복사 허용」): off unless the publisher turns it on. Existing
  bots read as off.

Downgrade drops the column.

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0071"
# re-parent onto the head current at merge time (0069 lands separately)
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "publications",
        sa.Column("allow_copy", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("publications", "allow_copy")
