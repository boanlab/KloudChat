"""Which tab has an untouched blank document open (`blank_doc_seen`).

A tab that has a 「빈 문서로 시작」 document open pings every minute; the server does not drop a
blank document another tab pinged recently, nor does the 6-hour sweep. One row per document and
tab. A table of its own, so a ping never moves `artifacts.updated_at` or `data` (either would read
as an edit). Rows go with their document (`ON DELETE CASCADE`).

Revision ID: 0105
Revises: 0104
Create Date: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("blank_doc_seen"):
        return
    op.create_table(
        "blank_doc_seen",
        sa.Column(
            "artifact_id", sa.String(32),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("tab", sa.String(40), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("blank_doc_seen")
