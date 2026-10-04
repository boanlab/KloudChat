"""다시 생성한 답은 앞의 답을 지우지 않고 데리고 간다.

같은 질문을 다시 돌리면 새 답 행이 앞 답들을 `superseded` 에 차례로 품는다.
화면은 ‹ k/n › 로 앞 답을 넘겨 보고, 대화는 마지막 답에서 이어진다.

Revision ID: 0046
Revises: 0045
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("superseded", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "superseded")
