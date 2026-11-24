"""The sheet surface: `sessionkind` gains `sheet`, `artifactkind` gains `xlsx`.

An `xlsx` artifact keeps its workbook in `storage_key`, one blob per version; `data` holds only
format, size and cell text for search.

Postgres only; elsewhere both are plain strings. `ADD VALUE` runs outside the migration
transaction (a new enum value cannot be used in the transaction that adds it); `IF NOT EXISTS`
makes a rerun harmless. Downgrade leaves the values (Postgres cannot drop one).

Revision ID: 0101
Revises: 0100
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0101"
down_revision: str | None = "0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE sessionkind ADD VALUE IF NOT EXISTS 'sheet'")
        op.execute("ALTER TYPE artifactkind ADD VALUE IF NOT EXISTS 'xlsx'")


def downgrade() -> None:
    pass
