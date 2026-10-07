"""Who opened a shared link: one row per reader visit.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "share_views",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("share_id", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opens", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("viewer_id", sa.String(), nullable=True),
        sa.Column("viewer_name", sa.String(), nullable=False, server_default=""),
        sa.Column("viewer_email", sa.String(), nullable=False, server_default=""),
        sa.Column("ip", sa.String(), nullable=False, server_default=""),
        sa.Column("user_agent", sa.String(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["share_id"], ["shares.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["viewer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_share_views_share_id", "share_views", ["share_id"])
    op.create_index("ix_share_views_viewer_id", "share_views", ["viewer_id"])
    # The read is always "this link's visits, newest first".
    op.create_index("ix_share_views_share_last", "share_views", ["share_id", "last_at"])


def downgrade() -> None:
    op.drop_index("ix_share_views_share_last", table_name="share_views")
    op.drop_index("ix_share_views_viewer_id", table_name="share_views")
    op.drop_index("ix_share_views_share_id", table_name="share_views")
    op.drop_table("share_views")
