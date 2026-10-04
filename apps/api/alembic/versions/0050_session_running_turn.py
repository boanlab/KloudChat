"""세션에 지금 도는 턴을 적어, 복제본이 달라도 한 번에 한 답만 쓰게 한다.

Revision ID: 0050
Revises: 0049
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("running_turn", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("running_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "running_since")
    op.drop_column("sessions", "running_turn")
