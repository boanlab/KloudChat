"""A question with nothing under it, and a record that says so.

A quarter of the conversations on the test account hold a user message and no
assistant reply. The user's sentence is written before the model answers, so a
turn that fails, is refused, or has its connection closed mid-answer leaves the
question behind on its own — and nothing anywhere says the answer never came.
Opened, such a conversation is a prompt with silence under it; in the list it
is indistinguishable from one that worked.

Keeping the question is right: the person did ask, and deleting their words
would be the dishonest fix. This column is the other half — the turn saying how
it ended. `'no_answer'` when nothing was produced, `'interrupted'` when the
stream broke after writing some of it, null for a turn that answered.

A plain string rather than a database enum, for the same reason `rating` is:
a third outcome should be a change to the model file.

No backfill. Every row that exists predates the recording, and a code invented
for them now would be a guess dressed as a fact — the transcript reads their
gap positionally instead, which is what it has to do anyway for the reader who
closes the tab while the answer is still arriving.

Revision ID: 0028
Revises: 0026
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("failure", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "failure")
