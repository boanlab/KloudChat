"""Which 시작점 a turn was begun from.

Picking a starting point used to type the template's whole framing into the
composer, so the transcript recorded it as the person's own words. It is now
carried beside the message instead, which leaves the question this column
answers: a year later, what was this turn started from?

`{"templateId": ..., "title": ...}` — the title alongside the id, because a
built-in id names nothing to a reader and a saved template can be deleted.
Never the prompt text: what the machinery was told is not what the person
said, and this table is the record of the second.

Nullable and null by default. Most turns start from nothing, and a turn that
did is not a turn missing a value.

No foreign key. Half the ids are built-ins that ship in the image and have no
row to point at, and the other half must survive their `templates` row being
removed — the whole reason the title is stored here.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("started_from", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "started_from")
