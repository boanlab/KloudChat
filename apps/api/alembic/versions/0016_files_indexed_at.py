"""When a document last reached the retrieval index.

`NULL` means the vector index does not cover it: attached before the index
existed, or indexed and failed. Lexical search covers it either way, so the
distinction is invisible without this column.

A timestamp rather than a boolean, so a document re-indexed after an
embedding-model change can be told from one indexed under the old model.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "files", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("files", "indexed_at")
