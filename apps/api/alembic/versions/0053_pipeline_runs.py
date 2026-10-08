"""Pipeline runs: each turn's stage records and per-stage bundle snapshots, so a run can be
resumed or run again from a stage in a later request.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "pipeline_runs",
        sa.Column("id", text(), nullable=False),
        sa.Column("session_id", text(), nullable=False),
        sa.Column("user_id", text(), nullable=False),
        sa.Column("message_id", text(), nullable=True),
        sa.Column("artifact_id", text(), nullable=True),
        sa.Column("artifact_version", sa.Integer(), nullable=True),
        sa.Column("parent_id", text(), nullable=True),
        sa.Column("from_stage", text(), nullable=True),
        sa.Column("pipeline", text(), nullable=False, server_default=""),
        sa.Column("flow", text(), nullable=False, server_default=""),
        sa.Column("status", text(), nullable=False, server_default="running"),
        sa.Column("params", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("stages", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("snapshots", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # A run goes with its session.
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_runs_session_id", "pipeline_runs", ["session_id"])
    op.create_index("ix_pipeline_runs_user_id", "pipeline_runs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_user_id", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_session_id", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
