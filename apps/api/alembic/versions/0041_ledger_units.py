"""사용량 원장이 크레딧이 아닌 양도 적는다.

Whisper 가 받아쓴 초, bge 가 임베딩한 청크 — 값이 0 크레딧인 모델의 일은
원장에 한 줄도 남지 않았고, 사용량 화면은 그 모델이 있는지도 몰랐다. 행마다
양과 단위를 적을 자리를 둔다; 크레딧이 0 이어도 행은 남는다.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credit_ledger", sa.Column("units", sa.Integer(), nullable=True))
    op.add_column("credit_ledger", sa.Column("unit", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("credit_ledger", "unit")
    op.drop_column("credit_ledger", "units")
