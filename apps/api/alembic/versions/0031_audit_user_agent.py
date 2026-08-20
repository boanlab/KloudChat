"""What the audit trail was looking at.

Every audit row already carried an address. None carried a browser, which is
the other half of "was that me": an address moves with a phone leaving the
building, and a sign-in from a browser nobody in the account has ever used is
the thing worth noticing. The admin's trail and a person's own 접속기록 read the
same rows, and both were answering the question with half the evidence.

Nothing is backfilled. Rows written before this have no browser and say so —
inventing one would put a claim in an audit log.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("user_agent", sa.String(), nullable=False, server_default=""),
    )
    # Every read of this table is "one actor's rows, newest first" — the admin
    # trail filtered by actor, and the account's own 접속기록.
    op.create_index("ix_audit_events_actor_at", "audit_events", ["actor_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor_at", table_name="audit_events")
    op.drop_column("audit_events", "user_agent")
