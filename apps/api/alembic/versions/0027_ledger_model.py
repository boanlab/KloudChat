"""어떤 모델에 쓴 크레딧인지, 원장에 기록.

The usage screen took its total from the ledger and its breakdown from stored
turns. Money that never produced a turn — pictures, clips, speech — was counted
in the total and absent from every bar beside it, so an account could spend
everything it had and read as "other".

Moving the breakdown onto the ledger answers the surface, which `session_id`
already knows. It does not answer the model, which the ledger never recorded.
This is that column.

Nullable, and deliberately not backfilled. A model inferred from the session is
wrong exactly where the money is: a fact-check bills the cheapest model the
account may use rather than the one the deck was written with, and a picture
bills whatever the request asked for. Writing that inference into an
append-only ledger would make it indistinguishable from a fact. Existing rows
keep their null, and the read falls back to the session's model for the three
media reasons — the only ones where the session is what quoted the price — so
an account with pictures and clips already in the table still sees them broken
out today, and sees them from the ledger itself once the rows are new.

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
    # Both usage screens now read this table by owner over a window, on every
    # load and for every range button.
    op.create_index(
        "ix_credit_ledger_user_created",
        "credit_ledger",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_credit_ledger_user_created", table_name="credit_ledger")
    op.drop_column("credit_ledger", "model")
