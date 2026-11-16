"""Web push: subscribed devices and the sent-notice log.

- `push_subscriptions`: one row per browser subscription (endpoint unique).
- `push_sent`: one row per notice, read for the per-user rate limit and for notices sent at most
  once (`once_key`, e.g. the credit warning of one cycle). Rows older than a day are pruned.

The VAPID key pair is not here: it is generated on first start into `system_settings`.

Revision ID: 0094
Revises: 0093
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(), nullable=False),
        sa.Column("auth", sa.String(), nullable=False),
        sa.Column("device", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])
    op.create_table(
        "push_sent",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event", sa.String(length=24), nullable=False),
        sa.Column("once_key", sa.String(length=120), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_push_sent_user_at", "push_sent", ["user_id", "at"])
    op.create_index("uq_push_sent_once", "push_sent", ["user_id", "once_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_push_sent_once", table_name="push_sent")
    op.drop_index("ix_push_sent_user_at", table_name="push_sent")
    op.drop_table("push_sent")
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
