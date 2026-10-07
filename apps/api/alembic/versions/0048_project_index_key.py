"""Retrieval-index collection for a project's knowledge and its conversations' uploads.

Revision ID: 0048
Revises: 0047
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("index_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "index_key")
