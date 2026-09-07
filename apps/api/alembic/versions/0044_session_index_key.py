"""대화에 올린 파일도 검색 색인에 들어간다.

에이전트 자료는 올리는 순간 색인되어 `search_knowledge` 가 벡터로 찾았지만,
대화 중에 올린 파일은 색인에 들어간 적이 없었다. 대화도 에이전트처럼
자기 색인 묶음 키를 하나 갖는다 — 처음 색인되는 업로드에서 만들어지고,
대화를 지울 때 묶음째 지운다.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("index_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "index_key")
