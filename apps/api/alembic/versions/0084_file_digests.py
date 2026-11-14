"""Per-file digests of project material.

- `files.digest`: a short digest of the file's text (label, title, summary, outline, key terms,
  and the text length it was made from), written by a strict-local model in the background.
- `files.digest_model`, `files.digested_at`: who wrote it and when.
- `files.digest_state`, `files.digest_attempts`, `files.digest_claimed_at`: the background job
  (`services/digest_jobs.py`), claimed the way OCR jobs are.

No existing rows change: every file starts without a digest, and one is asked for the first
time a turn uses the file's project (or by the administrator's backfill). Downgrade drops the
columns (digests are made again after an upgrade).

Revision ID: 0084
Revises: 0083
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("digest", "digest_model", "digested_at", "digest_state", "digest_attempts",
            "digest_claimed_at")


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)
    json_type = postgresql.JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    op.add_column("files", sa.Column("digest", json_type, nullable=True))
    op.add_column("files", sa.Column("digest_model", text(length=120), nullable=True))
    op.add_column("files", sa.Column("digested_at", ts, nullable=True))
    op.add_column("files", sa.Column("digest_state", text(length=16), nullable=True))
    op.add_column(
        "files", sa.Column("digest_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("files", sa.Column("digest_claimed_at", ts, nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("files", name)
