"""Governance policy, enforced rather than displayed.

One row per instance (`id = 'default'`): these are organisation-wide rules, not
per-user preferences, and an admin sets them once.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "governance",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("pii_masking", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("intent_filter", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "blocked_categories", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("governance")
