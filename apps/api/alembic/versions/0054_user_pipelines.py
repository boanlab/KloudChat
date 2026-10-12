"""User pipelines: pipelines people compose from agents, skills and checks, shared through
approval; agents and conversations bound to one.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "user_pipelines",
        sa.Column("id", text(), nullable=False),
        sa.Column("owner_id", text(), nullable=False),
        sa.Column("name", text(), nullable=False),
        sa.Column("description", text(), nullable=False, server_default=""),
        sa.Column("base", text(), nullable=True),
        sa.Column("origin_id", text(), nullable=True),
        sa.Column("stages", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("visibility", text(), nullable=False, server_default="private"),
        sa.Column("review", text(), nullable=False, server_default=""),
        sa.Column("review_note", text(), nullable=False, server_default=""),
        sa.Column("reviewed_by", text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("installs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_pipelines_owner_id", "user_pipelines", ["owner_id"])
    op.create_index("ix_user_pipelines_origin_id", "user_pipelines", ["origin_id"])
    # Nullable: an agent and a conversation keep running their shipped flow.
    op.add_column("agents", sa.Column("pipeline_id", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("pipeline_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "pipeline_id")
    op.drop_column("agents", "pipeline_id")
    op.drop_index("ix_user_pipelines_origin_id", table_name="user_pipelines")
    op.drop_index("ix_user_pipelines_owner_id", table_name="user_pipelines")
    op.drop_table("user_pipelines")
