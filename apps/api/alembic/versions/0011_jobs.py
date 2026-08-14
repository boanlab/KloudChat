"""Long-running generation jobs.

Pictures and speech come back inside the request that asked for them. Video does
not: the upstream returns a ticket and the clip arrives minutes later, so the
work has to survive the request, the tab, and a restart of this service.

The frontend already had a `Job` type, a job card and a `jobsApi` client. None of
them were connected to anything, because there was no table and no endpoint.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prompt", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("params", JSONB(), nullable=True),
        # The upstream's handle. Without it a restart orphans a clip that was
        # paid for and is still being made.
        sa.Column("provider_job_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("artifact_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("credits_estimated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credits_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_session_id", "jobs", ["session_id"])
    op.create_index("ix_jobs_provider_job_id", "jobs", ["provider_job_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_provider_job_id", table_name="jobs")
    op.drop_index("ix_jobs_session_id", table_name="jobs")
    op.drop_index("ix_jobs_user_id", table_name="jobs")
    op.drop_table("jobs")
