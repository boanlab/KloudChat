"""Governance switch keeping turns with internal material on strict-local models.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # On everywhere it lands: the rule only narrows where a turn may go.
    op.add_column(
        "governance",
        sa.Column(
            "internal_data_strict_local",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("governance", "internal_data_strict_local")
