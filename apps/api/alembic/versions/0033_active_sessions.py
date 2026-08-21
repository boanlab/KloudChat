"""Somewhere to see, and end, the sessions an account has open.

A refresh-token family already *was* a session — one browser on one machine,
rotating a cookie every quarter hour — but nothing on the row said which
machine, and no route listed the families or ended one. So an account signed in
on a lab PC could be signed out only from that PC, by somebody who was already
sitting at it. On a shared campus install that is the wrong way round: the
person who needs to end that session is the one who has walked away from it.

Three columns make a family describable — the address and browser it started
from, and when it was last seen — and the policy row gets an idle timeout, in
minutes, which the browser enforces by ending the session rather than renewing
it. 0 keeps the previous behaviour: a session lasts until its refresh cookie
expires.

Nothing is backfilled. Families that predate this list with no browser and no
address rather than a guessed one, and they still revoke.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("ip", sa.String(), nullable=False, server_default=""))
    op.add_column(
        "refresh_tokens", sa.Column("user_agent", sa.String(), nullable=False, server_default="")
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The session list is "this user's live families, newest first", and the
    # revoke is "this family". Both walk the same two columns.
    op.create_index("ix_refresh_tokens_user_created", "refresh_tokens", ["user_id", "created_at"])

    op.add_column(
        "governance",
        sa.Column("idle_timeout_minutes", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("governance", "idle_timeout_minutes")
    op.drop_index("ix_refresh_tokens_user_created", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "last_used_at")
    op.drop_column("refresh_tokens", "user_agent")
    op.drop_column("refresh_tokens", "ip")
