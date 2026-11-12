"""HWP documents as artifacts: `artifactkind` gains `hwp`.

An `hwp` artifact keeps its file (HWPX) in `storage_key`, one blob per version; `data`
holds only the format, size and body text for search.

Postgres only: elsewhere the kind is a plain string column. `ADD VALUE` runs outside the
migration's transaction (a new enum value cannot be used in the transaction that adds it),
and `IF NOT EXISTS` makes a rerun harmless. Downgrade leaves the value: Postgres cannot drop
one, and no row needs it gone.

Revision ID: 0078
Revises: 0077
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE artifactkind ADD VALUE IF NOT EXISTS 'hwp'")


def downgrade() -> None:
    pass
