"""User-issued API keys, and per-account model access.

Two things that both live on the proxy and both had to wait for per-user virtual
keys to exist:

* An **API key** a person can take away and use from a script. It is a second
  LiteLLM key of their own, so its spend lands on them like everything else.
* An **allowed model list** on the account, pushed to every key they hold. Until
  now everyone could reach every model the proxy served.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("allowed_models", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # Fernet ciphertext of the proxy key. Shown once at creation; after that
        # only the preview leaves the server.
        sa.Column("secret", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("preview", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_column("users", "allowed_models")
