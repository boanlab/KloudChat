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
    #: Auto, spending less: a turn a small model can answer is routed down.
    auto = "auto"
    #: Auto, spending more: a turn that needs the reasoning is routed up.
    #:
    #: The same classifier decides both. Cost reads its `low`; quality reads
    #: its `high` — one judgement, two directions, so a turn can never be
    #: called simple by one lane and hard by the other.
    auto_quality = "auto_quality"


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
    #: A generation that has stopped to ask something, and is waiting to be
    #: told to go on.
    #:
    #: Holds the request it began from, what was attached to it, and either the
    #: questions it needs answered or the outline it intends to write. Null on
    #: a session with nothing waiting, which is most of them most of the time.
    #:
    #: On the session rather than on the message that carries it, because it is
    #: session state: exactly one generation may be waiting at a time, a reload
    #: has to find it, and a second request while one is pending is an answer
    #: to it rather than a new document.
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

        #: What this turn produced, as artifact ids, for the turns whose answer is
        #: a thing rather than a sentence. The transcript renders them where the
        #: answer would be, so nothing has to be written *about* the picture.
        #:
        #: Ids and not a copy: an artifact is edited, versioned and deleted on its
        #: own, and a stale duplicate here would show a version nobody can reach.
    artifact_ids: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

        #: The 시작점 this turn began from: `{"templateId": ..., "title": ...}`.
        #: The title travels with the id so the transcript still names the template
        #: whether or not the row survived — a built-in id says nothing on its own.
        #:
        #: Never the template's prompt: what the model was told is not what the
        #: person said, and the transcript records the second.
    started_from: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    model: str | None = Field(default=None)

        #: What the person who owns this conversation thought of the answer. On
        #: the message rather than in a feedback table because it is read the way
        #: it is written: one turn at a time, in the transcript, by the reader who
        #: left it.
        #:
        #: Plain string rather than a database enum — a third verdict should be a
        #: migration of this file, not of the type behind it.
    rating: MessageRating | None = Field(default=None, sa_column=Column(String, nullable=True))

        #: Set when this turn did not produce the answer it was supposed to carry:
        #: on the assistant row when something was written before the stream broke,
        #: and on the question itself when nothing was. Null is the ordinary
        #: answered turn, and every row written before this was recorded.
        #:
        #: Rows predating it are read positionally instead — a question with
        #: nothing under it.
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
