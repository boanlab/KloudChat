"""에이전트가 첫 화면에서 쓰는 법을 말한다.

에이전트로 대화를 열면 이름과 한 줄 설명뿐이었다. 무엇을 가져와야 하는지,
한 턴에 무슨 일이 일어나는지, 무슨 말로 시작하면 되는지 — 안내 한 문단과
시작 문장 몇 개를 행에 둔다. 공유 방식도 같이: 가져가 편집하게 열지, 가져가되
지침은 비공개로 할지.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("guide", sa.String(), nullable=False, server_default=""))
    op.add_column("agents", sa.Column("starters", JSONB(), nullable=True))
    # 「가져가서 편집 가능하게 오픈할지, 가져갈 수는 있되 세부 내용을 비공개로
    # 할지」 — the sharer's choice, and the mark a sealed copy carries.
    op.add_column(
        "agents", sa.Column("share_mode", sa.String(), nullable=False, server_default="open")
    )
    op.add_column(
        "agents", sa.Column("sealed", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("agents", "sealed")
    op.drop_column("agents", "share_mode")
    op.drop_column("agents", "starters")
    op.drop_column("agents", "guide")
