"""Per-blank examples and requirements on user-written starting points.

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("templates", sa.Column("examples", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("templates", sa.Column("needs", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("templates", "needs")
    op.drop_column("templates", "examples")
