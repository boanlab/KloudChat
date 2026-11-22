"""Published agents answering in Slack.

- `slack_bot_channels`: a channel whose @mentions a published agent answers in a thread; `shared`
  marks a channel shared with another organisation (an outside audience).
- `slack_threads`: the bot conversation each Slack thread continues.
- `slack_workspaces.internal`: marked the organisation's own; any other workspace is an outside audience.

Downgrade drops both tables.

Revision ID: 0095
Revises: 0094
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)
    op.add_column("slack_workspaces", sa.Column("internal", sa.Boolean(), nullable=False,
                                                server_default=sa.false()))
    op.create_table(
        "slack_bot_channels",
        sa.Column("id", text(), primary_key=True),
        sa.Column("team_id", text(length=40), nullable=False),
        sa.Column("channel_id", text(length=40), nullable=False),
        sa.Column("channel_name", text(length=200), nullable=False, server_default=""),
        sa.Column("publication_id", text(),
                  sa.ForeignKey("publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_by", text(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("team_id", "channel_id", name="ux_slack_bot_channels"),
    )
    op.create_index("ix_slack_bot_channels_publication_id", "slack_bot_channels",
                    ["publication_id"])
    op.create_table(
        "slack_threads",
        sa.Column("id", text(), primary_key=True),
        sa.Column("key", text(length=120), nullable=False),
        sa.Column("session_id", text(length=32), nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_index("ix_slack_threads_key", "slack_threads", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_slack_threads_key", table_name="slack_threads")
    op.drop_table("slack_threads")
    op.drop_index("ix_slack_bot_channels_publication_id", table_name="slack_bot_channels")
    op.drop_table("slack_bot_channels")
    op.drop_column("slack_workspaces", "internal")
