"""A document says what it will be before it becomes it.

Two things went wrong together on the slides surface, and one column fixes the
join between them.

Generating wrote straight over whatever the session already had. A request the
model could not ground — an attached paper that arrived a third read, or a
one-line topic — still produced a deck, because the outline prompt told it in
so many words not to say the material was thin. So a paper somebody attached
came back as a presentation about presentations, in place of the deck they had
spent the afternoon on.

Every generation now stops at its outline and waits to be approved, and stops
earlier still — at a question — when it cannot ground what was asked. Neither
of those turns writes an artifact, which is what actually protects the existing
one: nothing can be replaced by a run that was never confirmed.

`pending` is where that half-finished turn lives between the two requests: what
was asked, what was attached to it, what the model wants to know or intends to
write. Null is a session with nothing waiting, which is every session that
exists today and most of them at any moment.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("pending", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "pending")
