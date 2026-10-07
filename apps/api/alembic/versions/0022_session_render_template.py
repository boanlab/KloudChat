"""The rendering template a session writes into.

Not a foreign key: the catalogue ships in the image, and an unknown id must degrade to
"no template".

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("render_template_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "render_template_id")
