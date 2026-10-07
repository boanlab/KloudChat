"""Read-only share links.

`scope` is `workspace` (signed in, any member) or `link` (public to whoever
holds the URL). The token is the capability: unguessable, revocable, and the
only thing the public route accepts.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shares",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # Exactly one is set: an artifact or a conversation.
        sa.Column("artifact_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # A real enum type: queries compare it to `ShareScope` members.
        sa.Column("scope", sa.Enum("workspace", "link", name="sharescope"), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Revoked rather than deleted, so the link's history stays visible.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The token is the whole authorisation, so it must be unique.
    op.create_unique_constraint("uq_shares_token", "shares", ["token"])
    op.create_index("ix_shares_owner_id", "shares", ["owner_id"])
    op.create_index("ix_shares_artifact_id", "shares", ["artifact_id"])


def downgrade() -> None:
    op.drop_index("ix_shares_artifact_id", table_name="shares")
    op.drop_index("ix_shares_owner_id", table_name="shares")
    op.drop_constraint("uq_shares_token", "shares", type_="unique")
    op.drop_table("shares")
    sa.Enum(name="sharescope").drop(op.get_bind(), checkfirst=True)
