"""Per-user LiteLLM virtual keys.

Each user carries their own key so the proxy's spend logs, budgets and rate
limits resolve to a person. These columns are where it lives.

`litellm_key` is Fernet ciphertext, not a key — see `services/settings_store`.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("litellm_key", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("litellm_key_preview", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("litellm_key_issued_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "litellm_key_issued_at")
    op.drop_column("users", "litellm_key_preview")
    op.drop_column("users", "litellm_key")
