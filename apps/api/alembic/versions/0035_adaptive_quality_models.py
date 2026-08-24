"""The models Auto may route *up* to, when a turn is worth more than it costs.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ordered, like the economy list beside it, and empty on every existing
    # installation: an upgrade lane that switched itself on would spend money
    # nobody agreed to.
    op.add_column(
        "governance",
        sa.Column(
            "adaptive_quality_model_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("governance", "adaptive_quality_model_ids")
