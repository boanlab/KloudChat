"""Agent output language, web-search default and memory opt-out.

- `agents.output_language`: language of the agent's turns and documents (`auto` = the request's language).
- `agents.web_search_default`: starting web search setting (`auto`, `on`, `off`).
- `agents.use_memory`: whether account memories are read into and written from the agent's turns.

Existing agents get `auto`, `auto`, true. Downgrade drops the columns.

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
