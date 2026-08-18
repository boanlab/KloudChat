"""The rendering template a session writes into.

A design template is a shape, not a prompt: picking one changes what comes out
of the surface, so it has to survive a reload the way the model choice does.

Nullable and null by default — a session with no template uses the built-in
track (markdown sections, JSON slides) exactly as before.

Not a foreign key: the catalogue ships inside the API image rather than in a
table, and an id that disappears in an upgrade must degrade to "no template"
rather than to a session that will not load.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("render_template_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "render_template_id")
