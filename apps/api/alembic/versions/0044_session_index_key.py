"""Retrieval-index collection for a conversation's uploads.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("index_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "index_key")
