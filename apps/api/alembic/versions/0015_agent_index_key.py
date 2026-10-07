"""Collection name for an agent's shelf in the retrieval index.

The name is the index's whole authorisation, so it is random rather than `agents.id`.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents", sa.Column("index_key", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    # Unique so two agents never share a shelf; partial because most agents have no key.
    op.create_index(
        "ux_agents_index_key",
        "agents",
        ["index_key"],
        unique=True,
        postgresql_where=sa.text("index_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_agents_index_key", table_name="agents")
    op.drop_column("agents", "index_key")
