"""What a turn produced, when what it produced is not a sentence.

The image and a/v surfaces wrote no message at all: the prompt became the
session's name and the picture was hung on the session, and the conversation
itself stayed empty. Opened, it was a blank screen with a panel beside it —
the one place in the product where what somebody typed did not appear where
they typed it.

The prompt can be stored as an ordinary user message. The reply cannot: a
picture is not a sentence, and prose invented about it would be the model
being quoted saying something it never said. This column is the third option
— the assistant row carries the ids of what it made, and the transcript
renders those where the answer goes.

Ids rather than a copy, because an artifact is versioned, edited and deleted
on its own, and a transcript holding a duplicate would be the one place
showing a version nobody can get back to.

No backfill. Ninety-odd picture and clip conversations predate the recording
and have no messages to attach ids to; their prompt survives as the title and
their result as `sessions.artifact_id`, which is what those rows have always
had and all this can honestly say about them.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("artifact_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "artifact_ids")
