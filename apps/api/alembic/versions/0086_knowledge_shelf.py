"""지식 서가: source selection, source guides, notes and studio outputs.

- `source_selections`: per conversation, the project sources it leaves out (none = all on).
- `source_guides`: a source's (or the project's) summary, key topics and suggested questions,
  made by a strict-local model on first look.
- `project_notes`: the project's 메모함 (hand-written, or pinned from an answer or a selection).
- `studio_items`: studio outputs (study guide, FAQ, briefing, timeline, glossary, mind map,
  quiz, flashcards, audio overview) with the sources they were grounded in.

New tables only; no existing row changes and no enum type is created (kinds and states are
short strings). Downgrade drops the four tables.

Revision ID: 0086
Revises: 0081
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0086"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)
    json_type = postgresql.JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()

    def user() -> sa.ForeignKey:
        return sa.ForeignKey("users.id")

    op.create_table(
        "source_selections",
        sa.Column("session_id", text(length=32), primary_key=True),
        sa.Column("user_id", text(), user(), nullable=False),
        sa.Column("off", json_type, nullable=True),
        sa.Column("updated_at", ts, nullable=False),
    )
    op.create_index("ix_source_selections_user_id", "source_selections", ["user_id"])
    op.create_table(
        "source_guides",
        sa.Column("id", text(), primary_key=True),
        sa.Column("user_id", text(), user(), nullable=False),
        sa.Column("project_id", text(length=32), nullable=False),
        sa.Column("subject", text(length=32), nullable=False),
        sa.Column("signature", text(length=64), nullable=False, server_default=""),
        sa.Column("state", text(length=12), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("topics", json_type, nullable=True),
        sa.Column("questions", json_type, nullable=True),
        sa.Column("model", text(length=120), nullable=False, server_default=""),
        sa.Column("error", text(length=200), nullable=False, server_default=""),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
        sa.UniqueConstraint("project_id", "subject", name="uq_source_guides_subject"),
    )
    op.create_index("ix_source_guides_user_id", "source_guides", ["user_id"])
    op.create_index("ix_source_guides_project_id", "source_guides", ["project_id"])
    op.create_table(
        "project_notes",
        sa.Column("id", text(), primary_key=True),
        sa.Column("user_id", text(), user(), nullable=False),
        sa.Column("project_id", text(length=32), nullable=False),
        sa.Column("title", text(length=200), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("origin", text(length=12), nullable=False, server_default="manual"),
        sa.Column("session_id", text(length=32), nullable=True),
        sa.Column("message_id", text(length=32), nullable=True),
        sa.Column("file_id", text(length=32), nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
    )
    op.create_index("ix_project_notes_user_id", "project_notes", ["user_id"])
    op.create_index("ix_project_notes_project_updated", "project_notes",
                    ["project_id", "updated_at"])
    op.create_table(
        "studio_items",
        sa.Column("id", text(), primary_key=True),
        sa.Column("user_id", text(), user(), nullable=False),
        sa.Column("project_id", text(length=32), nullable=False),
        sa.Column("kind", text(length=20), nullable=False),
        sa.Column("title", text(length=200), nullable=False, server_default=""),
        sa.Column("state", text(length=12), nullable=False, server_default="running"),
        sa.Column("speech_state", text(length=12), nullable=False, server_default=""),
        sa.Column("speech_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("speech_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_ids", json_type, nullable=True),
        sa.Column("options", json_type, nullable=True),
        sa.Column("content", json_type, nullable=True),
        sa.Column("artifact_id", text(length=32), nullable=True),
        sa.Column("session_id", text(length=32), nullable=True),
        sa.Column("audio_file_id", text(length=32), nullable=True),
        sa.Column("model", text(length=120), nullable=False, server_default=""),
        sa.Column("credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", text(length=200), nullable=False, server_default=""),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
    )
    op.create_index("ix_studio_items_user_id", "studio_items", ["user_id"])
    op.create_index("ix_studio_items_project_created", "studio_items",
                    ["project_id", "created_at"])


def downgrade() -> None:
    for table in ("studio_items", "project_notes", "source_guides", "source_selections"):
        op.drop_table(table)
