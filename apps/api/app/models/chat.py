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


class TurnFailure(StrEnum):
    """How a turn ended, when it did not end in an answer.

    Named for what the person is left holding rather than for what broke: the
    provider's error text belongs in the log, and the two outcomes a reader can
    tell apart on the screen — nothing at all, or half an answer — are also the
    only two that change what they do next.
    """

    no_answer = "no_answer"
    interrupted = "interrupted"


class MessageRating(StrEnum):
    """What a reader thought of one answer.

    Two values and a null, deliberately: null is "nobody said", which is not
    the same as an answer somebody looked at and found neither good nor bad.
    """

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

    #: The 시작점 this turn was begun from: `{"templateId": ..., "title": ...}`.
    #: The title travels with the id so a transcript read a year from now still
    #: names the template, whether or not the row behind it survived — and a
    #: built-in id says nothing to a reader on its own.
    #:
    #: Never the template's prompt. What the model was told is not what the
    #: person said, and the transcript is the record of the second.
    started_from: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    model: str | None = Field(default=None)

    #: What the person who owns this conversation thought of the answer. Kept
    #: on the message rather than in a feedback table because it is read the
    #: way it is written — one turn at a time, in the transcript, by the reader
    #: who left it and comes back to a long thread wanting to know which
    #: answers they had already decided against.
    #:
    #: Plain string rather than a database enum: a third verdict should be a
    #: migration of this file, not of the type behind it.
    rating: MessageRating | None = Field(default=None, sa_column=Column(String, nullable=True))

    #: Set when this turn did not produce the answer it was supposed to carry:
    #: on the assistant row when something was written before the stream broke,
    #: and on the question itself when nothing was. Null is the ordinary
    #: answered turn, and is also every row written before this was recorded.
    #:
    #: A reader who closes the tab mid-answer leaves nothing here — the request
    #: task is cancelled, so there is no moment left in which to write — and
    #: that gap is read positionally instead, as a question with nothing under
    #: it. Which is what makes the older rows legible too.
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
