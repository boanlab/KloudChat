"""대화에 켜 둔 스킬은 대화가 기억한다.

한 턴에 고른 스킬이 그 턴에만 적용되고 다음 턴에 사라지던 것을, 사람이 끄거나
바꿀 때까지 대화 전체에 이어지도록 세션에 둔다.

Revision ID: 0047
Revises: 0046
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("skill_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "skill_ids")
