"""A catalogue entry can be kept but not offered: `connector_catalog.listed`.

An unlisted entry stays installable by slug and installed copies keep working; administrators see it marked.

Data change, shipped rows only (matched by slug):

- `youtube`: unlisted.
- `deep-research`: disabled (it sends queries to the web for minutes at a time).
- `time`: seeded description replaced with one saying what the connector does.

Downgrade restores the three rows and drops the column.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIME_BEFORE = "현재 시각과 타임존 변환. '오늘'을 모델이 지어내지 않게 합니다."
TIME_AFTER = "현재 시각과 타임존 변환. '오늘'이 며칠인지 서버 시계로 알려 줍니다."

_catalog = sa.table(
    "connector_catalog",
    sa.column("slug", sa.String),
    sa.column("description", sa.String),
    sa.column("enabled", sa.Boolean),
    sa.column("listed", sa.Boolean),
)


def upgrade() -> None:
    op.add_column("connector_catalog", sa.Column(
        "listed", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute(_catalog.update().where(_catalog.c.slug == "youtube")
               .values(listed=sa.false()))
    op.execute(_catalog.update().where(_catalog.c.slug == "deep-research")
               .values(enabled=sa.false()))
    op.execute(_catalog.update().where(_catalog.c.slug == "time",
                                       _catalog.c.description == TIME_BEFORE)
               .values(description=TIME_AFTER))


def downgrade() -> None:
    op.execute(_catalog.update().where(_catalog.c.slug == "time",
                                       _catalog.c.description == TIME_AFTER)
               .values(description=TIME_BEFORE))
    op.execute(_catalog.update().where(_catalog.c.slug == "deep-research")
               .values(enabled=sa.true()))
    op.drop_column("connector_catalog", "listed")
