"""Quantity and unit on credit-ledger rows, so zero-credit work still appears in usage.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credit_ledger", sa.Column("units", sa.Integer(), nullable=True))
    op.add_column("credit_ledger", sa.Column("unit", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("credit_ledger", "unit")
    op.drop_column("credit_ledger", "units")
