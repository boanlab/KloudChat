"""A switch of its own for the lane that spends more.

One flag governed both directions, and it was named and labelled for the one
that saves — so turning on an upgrade path meant turning on cost routing with
it, and an instance that wanted only the upgrade could not have it.

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
    # Off everywhere it lands. The lane it governs has no candidates on any
    # existing installation, and one that did would be spending more than it
    # was asked to.
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
