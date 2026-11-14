"""The composer's reasoning chip, kept per conversation.

- `sessions.reasoning_mode`: `fast` or `deep` as picked on the composer; NULL is `auto` (the
  behaviour before the chip). A plain string column (no enum to cast).

Revision ID: 0088
Revises: 0087
Create Date: 2026-11-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("reasoning_mode", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "reasoning_mode")
