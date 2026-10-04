"""긴 대화의 앞부분을 요약해 두는 자리.

대화가 모델의 문맥 창을 넘으면 오래된 턴은 모델에 보내지 않고 요약으로 대신한다.
그 요약은 턴마다 다시 만들지 않고 세션에 둔다 — 어느 메시지까지 요약했는지와
요약문을 함께 두어, 다음에 더 잘라야 할 때 그 뒤의 턴만 보태어 다시 만든다.

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "summary")
