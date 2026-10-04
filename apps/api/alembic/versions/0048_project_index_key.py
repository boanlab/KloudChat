"""프로젝트 지식도 검색 색인에 들어간다.

에이전트 자료와 대화 업로드만 색인되고 프로젝트 파일은 어휘 검색뿐이었다. 프로젝트도
자기 색인 묶음 키를 갖는다 — 처음 색인되는 문서에서 만들어지고, 프로젝트 안 대화의
업로드도 같은 묶음에 들어가 한 번의 검색으로 함께 찾히며, 프로젝트를 지울 때 묶음째 지운다.

Revision ID: 0048
Revises: 0047
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("index_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "index_key")
