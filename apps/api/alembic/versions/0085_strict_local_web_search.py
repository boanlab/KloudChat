"""Web search in strict-local turns: ask, allow (queries only) or off.

- `governance.strict_local_web_search`: the admin's setting, `ask` by default — a strict-local
  turn that needs the web shows the queries that would leave and asks first.
- `projects.strict_local_web_search`: a project's own setting, only as strict as the admin's or
  stricter; null follows the admin.

Existing rows: the admin's setting starts at `ask` (before this, such turns never searched);
projects follow it. Downgrade drops both columns.

Revision ID: 0085
Revises: 0081
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0085"
down_revision: str | None = "0081"
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
