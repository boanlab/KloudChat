"""시작점의 빈칸마다 예시가 붙는다.

A card that listed five nouns and handed them to a placeholder was a card
that asked for a format nobody had been shown. The built-in starting points
now carry an example per blank and say what they cannot run without; a
starting point somebody wrote down should not ask worse questions than one
that shipped, so the row carries the same two lists.

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
