"""Agent usage guide, starter messages and share mode.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("guide", sa.String(), nullable=False, server_default=""))
    op.add_column("agents", sa.Column("starters", JSONB(), nullable=True))
    # `open` or `sealed` copies, and the mark a sealed copy carries.
    op.add_column(
        "agents", sa.Column("share_mode", sa.String(), nullable=False, server_default="open")
    )
    op.add_column(
        "agents", sa.Column("sealed", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("agents", "sealed")
    op.drop_column("agents", "share_mode")
    op.drop_column("agents", "starters")
    op.drop_column("agents", "guide")
