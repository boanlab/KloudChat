"""Surface on credit-ledger rows, and backfill of model and surface where recoverable.

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

    # The model from the newest artifact of the session that names one.
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
