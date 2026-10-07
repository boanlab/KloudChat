"""How a turn ended without an answer: `'no_answer'`, `'interrupted'`, or null.

Not backfilled; older rows are read positionally.

Revision ID: 0028
Revises: 0026
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("failure", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "failure")
