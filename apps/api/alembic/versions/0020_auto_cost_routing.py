"""Auto cost-routing policy and per-session mode.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("routing_mode", sa.String(), nullable=False, server_default="manual"),
    )
    op.add_column(
        "governance",
        sa.Column(
            "adaptive_routing_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "governance",
        sa.Column("adaptive_classifier_model_id", sa.String(), nullable=True),
    )
    op.add_column(
        "governance",
        sa.Column(
            "adaptive_economy_model_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("governance", "adaptive_economy_model_ids")
    op.drop_column("governance", "adaptive_classifier_model_id")
    op.drop_column("governance", "adaptive_routing_enabled")
    op.drop_column("sessions", "routing_mode")
