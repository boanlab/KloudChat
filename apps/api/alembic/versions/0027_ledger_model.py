"""Model a credit-ledger row paid for.

Not backfilled: a model inferred from the session would be wrong for some charges.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credit_ledger", sa.Column("model", sa.String(), nullable=True))
    # Usage screens read this table by owner over a time window.
    op.create_index(
        "ix_credit_ledger_user_created",
        "credit_ledger",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_credit_ledger_user_created", table_name="credit_ledger")
    op.drop_column("credit_ledger", "model")
