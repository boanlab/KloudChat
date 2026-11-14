"""지식 서가 (knowledge shelf) rows: per-conversation source selection, source guides, the
project's notes and its studio outputs. Each row is single-owner, like the workspace rows."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


def _uuid() -> str:
    return uuid.uuid4().hex


def _ts(**kwargs) -> Column:
    return Column(DateTime(timezone=True), **kwargs)


def _json(**kwargs) -> Column:
    return Column(JSONB, **kwargs)


class SourceSelection(SQLModel, table=True):
    """The project sources a conversation leaves out; no row (or an empty list) = all on.

    Stored as the switched-off ids so a source added later is on by default."""

    __tablename__ = "source_selections"

    session_id: str = Field(primary_key=True, max_length=32)
    user_id: str = Field(foreign_key="users.id", index=True)
    off: list | None = Field(default=None, sa_column=_json(nullable=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class SourceGuide(SQLModel, table=True):
    """A source's guide (summary, key topics, suggested questions), or the project's own
    (`subject = "project"`); made by a strict-local model on first look, kept until the
    material it was made from changes (`signature`)."""

    __tablename__ = "source_guides"
    __table_args__ = (UniqueConstraint("project_id", "subject", name="uq_source_guides_subject"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    project_id: str = Field(index=True, max_length=32)
    #: a file id, or "project"
    subject: str = Field(max_length=32)
    #: what it was made from (file ids and text lengths); differs → made again
    signature: str = Field(default="", max_length=64)
    #: running | done | failed
    state: str = Field(default="running", max_length=12)
    summary: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    topics: list | None = Field(default=None, sa_column=_json(nullable=True))
    questions: list | None = Field(default=None, sa_column=_json(nullable=True))
    model: str = Field(default="", max_length=120)
    error: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class ProjectNote(SQLModel, table=True):
    """A note in the project's 메모함: written by hand, or pinned from an answer or a selection."""

    __tablename__ = "project_notes"
    __table_args__ = (Index("ix_project_notes_project_updated", "project_id", "updated_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    project_id: str = Field(max_length=32)
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    #: manual | answer | selection
    origin: str = Field(default="manual", max_length=12)
    session_id: str | None = Field(default=None, max_length=32)
    message_id: str | None = Field(default=None, max_length=32)
    #: the project source made from this note, if any
    file_id: str | None = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class StudioItem(SQLModel, table=True):
    """One studio output of a project: a document (saved as an artifact too), or a quiz or
    flashcard set."""

    __tablename__ = "studio_items"
    __table_args__ = (Index("ix_studio_items_project_created", "project_id", "created_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    project_id: str = Field(max_length=32)
    #: study_guide | faq | briefing | timeline | glossary | mindmap | quiz | flashcards
    kind: str = Field(max_length=20)
    title: str = Field(default="", max_length=200)
    #: running | done | failed
    state: str = Field(default="running", max_length=12)
    source_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: the request's options (language, format, count, model, focus)
    options: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: the output (`services/studio.py` shapes per kind) and its numbered sources
    content: dict | None = Field(default=None, sa_column=_json(nullable=True))
    artifact_id: str | None = Field(default=None, max_length=32)
    session_id: str | None = Field(default=None, max_length=32)
    model: str = Field(default="", max_length=120)
    credits: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    error: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


__all__ = ["ProjectNote", "SourceGuide", "SourceSelection", "StudioItem"]
