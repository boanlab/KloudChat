"""시작점이 자기 서식을 데려온다.

결과 서식 was the other half of a two-tab dialogue: pick what you are doing,
then pick what it looks like. Two decisions for one job, and the second is a
question about typography asked of somebody who came to write an incident
report — which has a shape, and that shape is `doc-incident`.

So a starting point carries the 서식 its job comes out wearing. Empty stays a
real answer and the common one: a 동향 조사 has no house style, and then the
writing surfaces choose the colour and the impression from the subject.

Text rather than a foreign key, matching `sessions.render_template_id`: the
rendering catalogue ships in the image rather than in a table, so a release
that retires a 서식 has to leave the row readable instead of breaking the load.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("render_template_id", sa.String(length=60), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("templates", "render_template_id")
