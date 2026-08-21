"""좋아요 / 싫어요, kept.

The two buttons under every answer have been drawing themselves from local
state since they were added: a person marked an answer wrong, the thumb lit,
and the next reload forgot it. This column is where the verdict goes.

`'up'` / `'down'` / null, as a plain string rather than a database enum — a
third verdict should be a change to the model file, not a type migration.

Null is "nobody said" rather than "neither": an answer nobody rated and an
answer somebody weighed and shrugged at are different facts, and only the
first is true of every row that exists today. That is also why there is no
backfill — every existing message is unrated, which is what null already says.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("rating", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "rating")
