"""Video understanding: background media jobs, their resumable steps, and pieced uploads.

- `files.media_state`, `media_stage`, `media_done`, `media_total`, `media_attempts`,
  `media_claimed_at`, `media_claim`: the media job of an audio/video file (`video_jobs`),
  claimed the way OCR jobs are.
- `files.media`: probe facts, kept frames, timings and the time-coded segments.
- `media_parts`: each finished step (a transcribed audio piece, a read frame), so a job taken
  over after a restart resumes instead of starting again.
- `file_uploads`: uploads sent in pieces (videos up to MAX_VIDEO_UPLOAD_MB).

No existing rows change: files uploaded before keep their text and no media job. Downgrade
drops the tables and columns (time-coded documents already written stay as file text).

Revision ID: 0087
Revises: 0081
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0087"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("media_state", "media_stage", "media_done", "media_total", "media_attempts",
            "media_claimed_at", "media_claim", "media")


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)
    json_type = postgresql.JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    op.add_column("files", sa.Column("media_state", text(length=16), nullable=True))
    op.add_column("files", sa.Column("media_stage", text(length=16), nullable=True))
    for name in ("media_done", "media_total", "media_attempts"):
        op.add_column("files", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.add_column("files", sa.Column("media_claimed_at", ts, nullable=True))
    op.add_column("files", sa.Column("media_claim", text(length=32), nullable=True))
    op.add_column("files", sa.Column("media", json_type, nullable=True))
    op.create_table(
        "media_parts",
        sa.Column("id", text(), primary_key=True),
        sa.Column("file_id", text(), sa.ForeignKey("files.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", text(length=8), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("start", sa.Float(), nullable=False, server_default="0"),
        sa.Column("data", json_type, nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("file_id", "kind", "idx", name="uq_media_parts_file_kind_idx"),
    )
    op.create_index("ix_media_parts_file_id", "media_parts", ["file_id"])
    op.create_table(
        "file_uploads",
        sa.Column("id", text(), primary_key=True),
        sa.Column("user_id", text(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("mime", text(), nullable=False, server_default=""),
        sa.Column("project_id", text(), nullable=True),
        sa.Column("session_id", text(), nullable=True),
        sa.Column("received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_key", text(), nullable=False, server_default=""),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
    )
    op.create_index("ix_file_uploads_user_id", "file_uploads", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_file_uploads_user_id", table_name="file_uploads")
    op.drop_table("file_uploads")
    op.drop_index("ix_media_parts_file_id", table_name="media_parts")
    op.drop_table("media_parts")
    for name in reversed(_COLUMNS):
        op.drop_column("files", name)
