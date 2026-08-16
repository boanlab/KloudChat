"""Collection name for an agent's shelf in the retrieval index.

Every operation at the index is scoped to a collection name, so that name is the
whole authorisation. Not `agents.id`, which travels in URLs and API responses:
32 bytes of urlsafe randomness, same shape as a share token.

Nullable and filled on first use — an agent with no documents needs no shelf.

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
    # Unique so two agents can never share a shelf. Partial, because "no key
    # yet" is the normal state for an agent with nothing attached.
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
