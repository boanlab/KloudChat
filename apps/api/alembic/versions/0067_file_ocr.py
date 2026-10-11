"""Text from scanned pages: progress on the file row, and what each page was read as.

- `files.ocr_state` (null: the file needed no OCR), `ocr_total`, `ocr_done`, `ocr_review`,
  `ocr_attempts`, `ocr_claimed_at`: a background job reads the pages, claimed in the
  database so two API replicas never read the same file and a stale claim is taken over.
- `file_pages`: one row per page of a file that went through OCR (the reader, its score,
  whether the two readings disagreed, line boxes for later highlighting).

Existing rows are untouched: every file reads as one that needed no OCR. Downgrade drops
what this adds.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)
    json = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

    op.add_column("files", sa.Column("ocr_state", text(length=16), nullable=True))
    for name in ("ocr_total", "ocr_done", "ocr_review", "ocr_attempts"):
        op.add_column(
            "files", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column("files", sa.Column("ocr_claimed_at", ts, nullable=True))
    # The worker looks for open jobs only; a partial index keeps that cheap.
    op.create_index(
        "ix_files_ocr_open",
        "files",
        ["ocr_state"],
        postgresql_where=sa.text("ocr_state IN ('queued', 'running')"),
    )
    op.create_table(
        "file_pages",
        sa.Column("id", text(), primary_key=True),
        sa.Column(
            "file_id", text(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("source", text(length=16), nullable=False, server_default="text"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_table", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("layout", json, nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.UniqueConstraint("file_id", "page", name="uq_file_pages_file_page"),
    )
    op.create_index("ix_file_pages_file_id", "file_pages", ["file_id"])


def downgrade() -> None:
    op.drop_index("ix_file_pages_file_id", table_name="file_pages")
    op.drop_table("file_pages")
    op.drop_index("ix_files_ocr_open", table_name="files")
    for name in (
        "ocr_claimed_at",
        "ocr_attempts",
        "ocr_review",
        "ocr_done",
        "ocr_total",
        "ocr_state",
    ):
        op.drop_column("files", name)
