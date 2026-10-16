"""Where a file came from: uploaded by the person or made in a conversation.

- `files.origin`: 「upload」 (the person gave it: a chat attachment, a project or agent file, a
  page added by address) or 「generated」 (a code run's output, an image, a chart, a figure,
  audio, video, an export, a handoff's transcript). The account file box lists them apart.

Existing rows get 「upload」: nothing recorded how they came. Downgrade drops the column.

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("origin", sa.String(length=12), nullable=False, server_default="upload"),
    )


def downgrade() -> None:
    op.drop_column("files", "origin")
