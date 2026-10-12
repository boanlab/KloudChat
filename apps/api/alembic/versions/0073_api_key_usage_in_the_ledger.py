"""Member-held API key calls enter the credit ledger.

- `credit_ledger.external_ref`: the proxy's request id for a call made with a member's own API
  key (설정 › API 키). The usage sync writes one ledger row per such request, and the unique
  index keeps a request from being written twice. Null for every other row.

Downgrade drops the index and the column.

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credit_ledger", sa.Column("external_ref", sa.String(length=120), nullable=True))
    op.create_index("ux_credit_ledger_external_ref", "credit_ledger", ["external_ref"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_credit_ledger_external_ref", table_name="credit_ledger")
    op.drop_column("credit_ledger", "external_ref")
