"""Model comparison stores its alternatives on the message.

The alternatives belong with the turn that produced them — read and written
whole, never queried across rows, like `steps` and `usage`.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("variants", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "variants")
