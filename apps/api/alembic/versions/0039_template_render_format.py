"""The rendering template (서식) a starting point produces.

Text rather than a foreign key, matching `sessions.render_template_id`.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("render_template_id", sa.String(length=60), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("templates", "render_template_id")
