"""Web search in strict-local turns: ask, allow (queries only) or off.

- `governance.strict_local_web_search`: admin setting, `ask` by default (the turn shows the queries
  that would leave and asks first).
- `projects.strict_local_web_search`: a project's setting, at most as permissive as the admin's; null follows the admin.

Downgrade drops both columns.

Revision ID: 0085
Revises: 0084
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "governance",
        sa.Column("strict_local_web_search", sa.String(), nullable=False, server_default="ask"),
    )
    op.add_column(
        "projects", sa.Column("strict_local_web_search", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "strict_local_web_search")
    op.drop_column("governance", "strict_local_web_search")
