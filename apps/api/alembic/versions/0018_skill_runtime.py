"""Explicit per-turn skills and stable built-in catalogue metadata.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("catalog_key", sa.String(), nullable=True))
    op.add_column(
        "skills",
        sa.Column("required_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "skills",
        sa.Column("estimated_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "uq_skills_owner_catalog_key",
        "skills",
        ["owner_id", "catalog_key"],
        unique=True,
        postgresql_where=sa.text("catalog_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_skills_owner_catalog_key", table_name="skills")
    op.drop_column("skills", "estimated_tokens")
    op.drop_column("skills", "required_tools")
    op.drop_column("skills", "catalog_key")
