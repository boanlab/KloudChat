"""Sessions and messages.

One `sessions` table carries all five surfaces, discriminated by `kind` — the
sidebar's recent list, project membership, and search are then one query rather
than five. Only `chat` streams today; the other kinds get their execution paths
in later phases but already persist here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


def _uuid() -> str:
    return uuid.uuid4().hex


def _ts_column(**kwargs) -> Column:
    return Column(DateTime(timezone=True), **kwargs)


class SessionKind(StrEnum):
    chat = "chat"
    report = "report"
    slides = "slides"
    image = "image"
    # Audio and video share one surface — both are timeline media produced by
    # the same long-running job path. See src/types.ts.
    av = "av"


class RoutingMode(StrEnum):
    manual = "manual"
    auto = "auto"


class Role(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class ChatSession(SQLModel, table=True):
    """Named `ChatSession` because `Session` collides with SQLAlchemy's."""

    __tablename__ = "sessions"
    __table_args__ = (
        # The sidebar query: this user's sessions, newest first.
        Index("ix_sessions_user_updated", "user_id", "updated_at"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    kind: SessionKind = Field(default=SessionKind.chat)
    title: str = Field(default="")
    project_id: str | None = Field(default=None)
    agent_id: str | None = Field(default=None)
    model: str = Field(default="")
    #: ``model`` always remains the real quality model. Auto routing is a
    #: separate mode so a virtual picker entry can never leak to LiteLLM.
    routing_mode: RoutingMode = Field(
        default=RoutingMode.manual,
        sa_column=Column(String, nullable=False, default=RoutingMode.manual.value),
    )
    artifact_id: str | None = Field(default=None)
    #: The rendering template this session writes into, when one was picked.
    #: A plain string rather than a foreign key: the catalogue ships inside the
    #: image, so there is no row to point at, and an id that stops existing
    #: after an upgrade has to degrade to "no template" rather than to a
    #: session that will not load.
    render_template_id: str | None = Field(default=None)
    pinned: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    role: Role
    content: str = Field(default="")

    # JSONB rather than side tables: these are read and written whole, always
    # with their message, and never queried across rows.
    steps: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    attachments: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    usage: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: Model comparison: one entry per model that answered this turn. Null for an
    #: ordinary turn, which is every turn that did not go through /compare.
    variants: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: Requested/effective model and privacy action. Contains no prompt text or
    #: detected value, and is safe to return with the transcript.
    routing: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    model: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


__all__ = ["ChatSession", "Message", "Role", "RoutingMode", "SessionKind"]
