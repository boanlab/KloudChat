"""Reader rating on an answer: `'up'`, `'down'` or null, as a plain string.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("rating", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "rating")
