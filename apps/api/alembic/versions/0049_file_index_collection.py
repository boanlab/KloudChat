"""The index collection each file was written to, so a move or delete forgets the right one.

Revision ID: 0049
Revises: 0048
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("files", sa.Column("index_collection", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("files", "index_collection")
