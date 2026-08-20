"""The formats a project's work starts in.

`sessions.render_template_id` is per conversation and picked in the composer,
so until now a project could carry a look but never a shape: "this project's
reports are always the 공문 form" had to be said again in every new session.

One JSONB map rather than a column per surface — `models.workspace.Project`
argues that choice where the column is declared. Nullable and null by default:
a project that never chose a format keeps the built-in track exactly.

No foreign key, and no check constraint on the keys. The catalogue ships in
the image rather than in a table, and the router refuses an id it cannot place
on write; what the database can usefully promise here is that the column holds
JSON.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("render_templates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "render_templates")
