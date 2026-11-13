"""Index deletes that failed, kept for retry.

- `index_deletions`: one row per search-index entry (a document in a collection, or a whole
  collection when `doc_id` is null) whose delete the index did not take. A background loop
  retries each until the index answers; the file or conversation row it came from is gone.

No existing rows change. Downgrade drops the table (pending retries are forgotten).

Revision ID: 0081
Revises: 0080
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)
    op.create_table(
        "index_deletions",
        sa.Column("id", text(), primary_key=True),
        sa.Column("collection", text(length=64), nullable=False),
        sa.Column("doc_id", text(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("tried_at", ts, nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("index_deletions")
