"""Which surface a charge came from, and which model earned it.

Every media charge was written with no model on it. The usage screen recovered
one by looking at the session's own model, which works while the conversation
exists and files the charge under 기타 the moment somebody deletes it — and the
surface went the same way, since that was read off the session too.

A ledger row should say what it paid for without depending on anything else
surviving. `model` has been on the row since 0027 and the media routes now
write it; `surface` is new here for the same reason.

Backfilled where it can be recovered: the model from the artifact the charge
produced, the surface from the session that is still there. Rows whose session
and artifact are both gone keep their nulls — there is nothing left that knows,
and a guess in a ledger is worse than a gap.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEDIA = "('video.generate', 'image.generate', 'audio.generate')"


def upgrade() -> None:
    op.add_column("credit_ledger", sa.Column("surface", sa.String(), nullable=True))

    # The model the charge actually paid, from the artifact it produced. One
    # media session can hold several, so this takes the newest that names a
    # model — a batch is one generator at one price.
    op.execute(f"""
        UPDATE credit_ledger l
           SET model = sub.model
          FROM (
            SELECT DISTINCT ON (a.session_id)
                   a.session_id, a.data->>'model' AS model
              FROM artifacts a
             WHERE a.session_id IS NOT NULL AND a.data->>'model' <> ''
             ORDER BY a.session_id, a.created_at DESC
          ) sub
         WHERE l.model IS NULL
           AND l.session_id = sub.session_id
           AND l.reason IN {_MEDIA}
    """)

    # And from the job, for the clips that have one.
    op.execute("""
        UPDATE credit_ledger l
           SET model = j.model
          FROM jobs j
         WHERE l.model IS NULL AND l.job_id = j.id AND j.model <> ''
    """)

    # The surface, from the conversation while it is still there.
    op.execute("""
        UPDATE credit_ledger l
           SET surface = s.kind
          FROM sessions s
         WHERE l.surface IS NULL AND l.session_id = s.id
    """)


def downgrade() -> None:
    op.drop_column("credit_ledger", "surface")
