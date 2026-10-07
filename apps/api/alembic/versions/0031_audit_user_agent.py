"""Raw `User-Agent` on audit events. Older rows are left empty.

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
    # Reads are "one actor's rows, newest first".
    op.create_index("ix_audit_events_actor_at", "audit_events", ["actor_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor_at", table_name="audit_events")
    op.drop_column("audit_events", "user_agent")
