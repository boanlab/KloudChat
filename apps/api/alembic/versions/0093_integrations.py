"""Google and Slack connections, the writes that wait for the person's confirmation, and
Drive files brought in as material.

- `integration_accounts`: one row per person and provider: sealed access and refresh tokens,
  granted scopes, the authorization in progress (hashed state, sealed PKCE verifier), choices.
- `slack_workspaces`: a Slack workspace where the app is installed, with its sealed bot token
  (installed by the first person who connects there, when the workspace allows it).
- `integration_imports`: a Drive file copied in as a stored file (project material or an
  attachment), with its Drive id and modified time, so it can be synced again.
- `integration_actions`: a write the agent proposed (a calendar event, a Drive file, a Slack
  message); nothing reaches the provider until the person confirms it.

No existing rows change. Downgrade drops the four tables (connections are forgotten; the
providers keep the grants until people revoke them there).

Revision ID: 0093
Revises: 0092
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)
    json = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

    op.create_table(
        "integration_accounts",
        sa.Column("id", text(), primary_key=True),
        sa.Column("user_id", text(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("provider", text(length=20), nullable=False),
        sa.Column("status", text(length=20), nullable=False, server_default="pending"),
        sa.Column("label", text(length=200), nullable=False, server_default=""),
        sa.Column("external_id", text(length=120), nullable=False, server_default=""),
        sa.Column("team_id", text(length=40), nullable=False, server_default=""),
        sa.Column("scopes", json, nullable=True),
        sa.Column("access_token", text(), nullable=False, server_default=""),
        sa.Column("refresh_token", text(), nullable=False, server_default=""),
        sa.Column("expires_at", ts, nullable=True),
        sa.Column("pending_state", text(length=64), nullable=False, server_default=""),
        sa.Column("pending_verifier", text(), nullable=False, server_default=""),
        sa.Column("pending_at", ts, nullable=True),
        sa.Column("pending_browser", text(length=64), nullable=False, server_default=""),
        sa.Column("prefs", json, nullable=True),
        sa.Column("last_used_at", ts, nullable=True),
        sa.Column("error", text(length=300), nullable=False, server_default=""),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
        sa.UniqueConstraint("user_id", "provider", name="ux_integration_accounts"),
    )
    op.create_index("ix_integration_accounts_user_id", "integration_accounts", ["user_id"])
    op.create_index("ix_integration_accounts_pending_state", "integration_accounts",
                    ["pending_state"])

    op.create_table(
        "slack_workspaces",
        sa.Column("team_id", text(length=40), primary_key=True),
        sa.Column("name", text(length=200), nullable=False, server_default=""),
        sa.Column("bot_token", text(), nullable=False, server_default=""),
        sa.Column("bot_user_id", text(length=40), nullable=False, server_default=""),
        sa.Column("scopes", json, nullable=True),
        sa.Column("installed_by", text(length=32), nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
    )

    op.create_table(
        "integration_actions",
        sa.Column("id", text(), primary_key=True),
        sa.Column("user_id", text(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("session_id", text(length=32), nullable=True),
        sa.Column("provider", text(length=20), nullable=False),
        sa.Column("kind", text(length=40), nullable=False),
        sa.Column("payload", json, nullable=True),
        sa.Column("status", text(length=20), nullable=False, server_default="pending"),
        sa.Column("result", json, nullable=True),
        sa.Column("error", text(length=300), nullable=False, server_default=""),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("decided_at", ts, nullable=True),
    )
    op.create_index("ix_integration_actions_user_created", "integration_actions",
                    ["user_id", "created_at"])


    op.create_table(
        "integration_imports",
        sa.Column("id", text(), primary_key=True),
        sa.Column("user_id", text(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("file_id", text(length=32), nullable=False),
        sa.Column("project_id", text(length=32), nullable=True),
        sa.Column("provider", text(length=20), nullable=False, server_default="google"),
        sa.Column("external_id", text(length=200), nullable=False),
        sa.Column("mime", text(length=200), nullable=False, server_default=""),
        sa.Column("modified", text(length=40), nullable=False, server_default=""),
        sa.Column("source_url", text(length=500), nullable=False, server_default=""),
        sa.Column("synced_at", ts, nullable=False),
        sa.Column("created_at", ts, nullable=False),
    )
    op.create_index("ix_integration_imports_file_id", "integration_imports", ["file_id"])
    op.create_index("ix_integration_imports_user_external", "integration_imports",
                    ["user_id", "external_id"])


def downgrade() -> None:
    op.drop_index("ix_integration_imports_user_external", table_name="integration_imports")
    op.drop_index("ix_integration_imports_file_id", table_name="integration_imports")
    op.drop_table("integration_imports")
    op.drop_index("ix_integration_actions_user_created", table_name="integration_actions")
    op.drop_table("integration_actions")
    op.drop_table("slack_workspaces")
    op.drop_index("ix_integration_accounts_pending_state", table_name="integration_accounts")
    op.drop_index("ix_integration_accounts_user_id", table_name="integration_accounts")
    op.drop_table("integration_accounts")
