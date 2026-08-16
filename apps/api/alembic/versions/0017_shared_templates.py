"""Templates an administrator provides to everyone.

A template written from the gallery belongs to the person who wrote it. An
organisation's own 공문 or 발표 양식 is not that: one person should enter it and
every account should see it.

`shared` is the flag, settable only by an administrator, and the gallery reads
"mine, plus everything shared". Sharing rather than copying — a copy taken at
signup would not follow a correction to the form.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Every gallery open reads the shared set.
    op.create_index(
        "ix_templates_shared", "templates", ["shared"], postgresql_where=sa.text("shared")
    )


def downgrade() -> None:
    op.drop_index("ix_templates_shared", table_name="templates")
    op.drop_column("templates", "shared")
