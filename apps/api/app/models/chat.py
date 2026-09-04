"""Sessions and messages. One `sessions` table carries every surface, discriminated by `kind`."""

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
    # Audio and video share one surface; mirrored in the web client's types.
    av = "av"


class RoutingMode(StrEnum):
    manual = "manual"
    #: Routes simple turns down to an economy model.
    auto = "auto"
    #: Routes hard turns up to a quality model. Same classifier as `auto`.
    auto_quality = "auto_quality"


class Role(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class TurnFailure(StrEnum):
    """How a turn ended when it did not end in an answer."""

    no_answer = "no_answer"
    interrupted = "interrupted"
    #: Stop pressed by the user: a kept partial reply that the screen must not call a failure.
    stopped = "stopped"


class MessageRating(StrEnum):
    """Reader verdict on one answer; null on the message means "not rated"."""

    up = "up"
    down = "down"


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
    #: ``model`` is always a real model id; auto routing is a separate mode so a
    #: virtual picker entry never reaches LiteLLM.
    routing_mode: RoutingMode = Field(
        default=RoutingMode.manual,
        sa_column=Column(String, nullable=False, default=RoutingMode.manual.value),
    )
    artifact_id: str | None = Field(default=None)
    #: Rendering template id. Not a foreign key: the catalogue ships in the image,
    #: and an unknown id must degrade to "no template".
    render_template_id: str | None = Field(default=None)
    #: A generation paused to ask something: its request, attachments, and the
    #: questions or outline it is waiting on. At most one per session.
    pending: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
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

    # JSONB: read and written whole with the message, never queried across rows.
    steps: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    attachments: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    usage: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: Model comparison: one entry per model that answered. Null outside /compare.
    variants: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: Requested/effective model and privacy action. Contains no prompt text or
    #: detected value, and is safe to return with the transcript.
    routing: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: Artifacts this turn produced, rendered in place of a text answer. Ids
    #: only: artifacts are versioned and deleted on their own.
    artifact_ids: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: Prompt template this turn began from: `{"templateId": ..., "title": ...}`.
    #: The title is copied so the transcript names it after the row is gone.
    #: Never the template's prompt text.
    started_from: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    model: str | None = Field(default=None)

    #: Owner's verdict on the answer. Plain string column, not a database enum.
    rating: MessageRating | None = Field(default=None, sa_column=Column(String, nullable=True))

    #: On the assistant row when a partial reply was kept, on the user row when
    #: nothing was written. Null: answered normally, or a row older than the
    #: column (those are read positionally as a question with nothing under it).
    failure: TurnFailure | None = Field(default=None, sa_column=Column(String, nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


__all__ = [
    "ChatSession",
    "Message",
    "MessageRating",
    "Role",
    "RoutingMode",
    "SessionKind",
    "TurnFailure",
]
