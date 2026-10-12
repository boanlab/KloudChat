"""Skills carry what the server runs for them; skills with scripts wait for review to be
shared; agents carry the questions they ask before writing.

- `skills.server_fields`: `stage`, `entry`, `post_check`, `post_judge`, `copy_check`,
  `copy_kinds`, `tables`, `own_stage`, `uses` (`services/skill_specs.py`), each naming
  shipped scripts only, so a person's skill opts in the way a shipped SKILL.md does. Null
  for every existing row: a shipped copy that stores none reads its package, and the
  seeder fills the catalogue's own rows.
- `skills.script_files`: a person's own scripts, run only in the code sandbox.
- `skills.review`, `review_note`, `reviewed_by`, `reviewed_at`: sharing a skill with
  scripts waits for an administrator.
- `agents.gap_questions`: 빠진 정보 질문, what an agent asks before writing
  (`services/gap_questions.py`). Null for every existing row: an agent with none stored asks
  what its 서식 and the official agent it runs as ask (read from their files), and the seeder
  fills the catalogue's own rows.

Schema only: no row is changed. Downgrade drops the columns.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json = JSONB().with_variant(sa.JSON(), "sqlite")
    op.add_column("skills", sa.Column("server_fields", json, nullable=True))
    op.add_column("skills", sa.Column("script_files", json, nullable=True))
    op.add_column(
        "skills", sa.Column("review", sa.String(length=20), nullable=False, server_default="")
    )
    op.add_column(
        "skills", sa.Column("review_note", sa.String(), nullable=False, server_default="")
    )
    op.add_column("skills", sa.Column("reviewed_by", sa.String(), nullable=True))
    op.add_column("skills", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agents", sa.Column("gap_questions", json, nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "gap_questions")
    op.drop_column("skills", "reviewed_at")
    op.drop_column("skills", "reviewed_by")
    op.drop_column("skills", "review_note")
    op.drop_column("skills", "review")
    op.drop_column("skills", "script_files")
    op.drop_column("skills", "server_fields")
