"""Issue reports (「문의·신고」) and their reply threads.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    json = JSONB().with_variant(sa.JSON(), "sqlite")
    op.create_table(
        "issue_reports",
        sa.Column("id", text(), nullable=False),
        sa.Column("user_id", text(), nullable=False),
        sa.Column("kind", text(), nullable=False, server_default="bug"),
        sa.Column("status", text(), nullable=False, server_default="received"),
        sa.Column("body", text(), nullable=False, server_default=""),
        sa.Column("assignee_id", text(), nullable=True),
        sa.Column("hold_reason", text(), nullable=False, server_default=""),
        sa.Column("session_id", text(), nullable=True),
        sa.Column("message_id", text(), nullable=True),
        sa.Column("artifact_id", text(), nullable=True),
        sa.Column("session_shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("context", json, nullable=True),
        sa.Column("snapshot", json, nullable=True),
        sa.Column("snapshot_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("screenshot_key", text(), nullable=True),
        sa.Column("screenshot_name", text(), nullable=False, server_default=""),
        sa.Column("screenshot_mime", text(), nullable=False, server_default=""),
        sa.Column("admin_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_issue_reports_user_id", "issue_reports", ["user_id"])
    op.create_index(
        "ix_issue_reports_status_created", "issue_reports", ["status", "created_at"]
    )
    op.create_table(
        "issue_replies",
        sa.Column("id", text(), nullable=False),
        sa.Column("report_id", text(), nullable=False),
        sa.Column("author_id", text(), nullable=True),
        sa.Column("author_role", text(), nullable=False, server_default="user"),
        sa.Column("author_name", text(), nullable=False, server_default=""),
        sa.Column("body", text(), nullable=False, server_default=""),
        sa.Column("status", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["issue_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_issue_replies_report_id", "issue_replies", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_issue_replies_report_id", table_name="issue_replies")
    op.drop_table("issue_replies")
    op.drop_index("ix_issue_reports_status_created", table_name="issue_reports")
    op.drop_index("ix_issue_reports_user_id", table_name="issue_reports")
    op.drop_table("issue_reports")
