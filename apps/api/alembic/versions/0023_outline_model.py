"""The model that plans a document, when it should not be the one that writes it.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("governance", sa.Column("outline_model_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("governance", "outline_model_id")
