"""Channels for published agents open outside: framed on the owner's sites, an
OpenAI-compatible API and an MCP server, each opened by an administrator.

- `publications.channels`: the channels in force (`{"embed": {"origins": [...]}, "api": {},
  "mcp": {}}`); `requested_channels`: the set asked for while a review is pending.
- `publication_keys`: keys an owner issued for one bot's API and MCP channels, kept as hashes.

No existing row changes: no bot has a channel. Downgrade drops what this adds.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)
    json = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

    op.add_column("publications", sa.Column("channels", json, nullable=True))
    op.add_column("publications", sa.Column("requested_channels", json, nullable=True))
    op.create_table(
        "publication_keys",
        sa.Column("id", text(), primary_key=True),
        sa.Column("publication_id", text(),
                  sa.ForeignKey("publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", text(length=80), nullable=False, server_default=""),
        sa.Column("key_hash", text(length=64), nullable=False),
        sa.Column("preview", text(length=40), nullable=False, server_default=""),
        sa.Column("created_by", text(), nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("last_used_at", ts, nullable=True),
        sa.Column("revoked_at", ts, nullable=True),
    )
    op.create_index("ix_publication_keys_publication_id", "publication_keys",
                    ["publication_id"])
    op.create_index("ix_publication_keys_key_hash", "publication_keys", ["key_hash"],
                    unique=True)


def downgrade() -> None:
    op.drop_index("ix_publication_keys_key_hash", table_name="publication_keys")
    op.drop_index("ix_publication_keys_publication_id", table_name="publication_keys")
    op.drop_table("publication_keys")
    op.drop_column("publications", "requested_channels")
    op.drop_column("publications", "channels")
