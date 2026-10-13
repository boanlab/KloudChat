"""Agent output language, web-search default and memory opt-out.

- `agents.output_language`: the language every turn and document of the agent's conversations
  is written in (`auto` = the request's language, as before).
- `agents.web_search_default`: the web search setting a new conversation with the agent starts
  with (`auto`, `on`, `off`); the person still changes it per turn.
- `agents.use_memory`: whether account memories are read into the agent's turns and written
  from them.

Every existing agent gets the values that keep today's behaviour (`auto`, `auto`, true).
Downgrade drops the three columns.

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("output_language", sa.String(length=40), nullable=False, server_default="auto"),
    )
    op.add_column(
        "agents",
        sa.Column("web_search_default", sa.String(length=10), nullable=False,
                  server_default="auto"),
    )
    op.add_column(
        "agents",
        sa.Column("use_memory", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("agents", "use_memory")
    op.drop_column("agents", "web_search_default")
    op.drop_column("agents", "output_language")
