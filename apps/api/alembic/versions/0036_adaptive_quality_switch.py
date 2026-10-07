"""A separate switch for quality-upgrade auto routing.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Off by default: the lane spends more than the chosen model.
    op.add_column(
        "governance",
        sa.Column(
            "adaptive_quality_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("governance", "adaptive_quality_enabled")
