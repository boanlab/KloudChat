"""Where a file came from: `files.origin` is `upload` (attachment, project or agent file, added page) or
`generated` (code output, image, chart, figure, audio, video, export, handoff transcript).

Existing rows get `upload`. Downgrade drops the column.

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
