"""Password reset tickets.

The sign-in page carried a "비밀번호를 잊으셨나요?" ("forgot your password?")
link with no handler behind it, because there was no way to reset one: the API
could change a password given the current one, and nothing else. This is the
table that makes the link real.

Only the hash of each token is stored. A table of live reset tokens is a table of
working passwords, and the realistic exposure for those is a database dump.

Rows survive their use. `used_at` is what separates a second click on the same
link from a link that never existed, and the two deserve different answers.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        # Kept for the audit trail: a burst of requests for one account from one
        # address is the shape of someone probing it.
        sa.Column("requested_ip", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_password_resets_user_id", "password_resets", ["user_id"])
    op.create_index(
        "ix_password_resets_token_hash", "password_resets", ["token_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_password_resets_token_hash", table_name="password_resets")
    op.drop_index("ix_password_resets_user_id", table_name="password_resets")
    op.drop_table("password_resets")
