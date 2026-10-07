"""Refresh-token family details for the active-session list, and an idle timeout policy.

The idle timeout is in minutes; 0 disables it.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("ip", sa.String(), nullable=False, server_default=""))
    op.add_column(
        "refresh_tokens", sa.Column("user_agent", sa.String(), nullable=False, server_default="")
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The session list and the family revoke both read these two columns.
    op.create_index("ix_refresh_tokens_user_created", "refresh_tokens", ["user_id", "created_at"])

    op.add_column(
        "governance",
        sa.Column("idle_timeout_minutes", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("governance", "idle_timeout_minutes")
    op.drop_index("ix_refresh_tokens_user_created", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "last_used_at")
    op.drop_column("refresh_tokens", "user_agent")
    op.drop_column("refresh_tokens", "ip")
