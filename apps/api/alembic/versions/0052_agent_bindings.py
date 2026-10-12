"""Agent bindings: the surface, 서식, document format and design an agent opens with.

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("surface", "render_template_id", "doc_format", "design_system_id")


def upgrade() -> None:
    # All nullable: an existing agent keeps opening the way it did.
    for name in _COLUMNS:
        op.add_column("agents", sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("agents", name)
