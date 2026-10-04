"""파일이 어느 색인 묶음에 들어갔는지 기록한다.

대화를 다른 프로젝트로 옮기거나 지울 때 옛 묶음에 벡터가 남았다. 파일마다 실제로 들어간
묶음 키를 적어 두어, 옮기면 옛 묶음에서 빼고 새 묶음에 다시 넣고, 지우면 그 묶음에서 뺀다.

Revision ID: 0049
Revises: 0048
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("files", sa.Column("index_collection", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("files", "index_collection")
