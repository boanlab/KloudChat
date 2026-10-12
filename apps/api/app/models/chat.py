"""Sessions and messages; one `sessions` table for every surface, by `kind`."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Column, DateTime, Index, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel, col

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
    # audio+video surface; mirrored in web types
    av = "av"


class RoutingMode(StrEnum):
    manual = "manual"
    #: simple turns down to economy model
    auto = "auto"
    #: hard turns up to quality model (same classifier)
    auto_quality = "auto_quality"


class Role(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class TurnFailure(StrEnum):
    """Non-answer turn ending."""

    no_answer = "no_answer"
    interrupted = "interrupted"
    #: user stop; kept partial reply, not a failure
    stopped = "stopped"


class MessageRating(StrEnum):
    """Reader verdict; null = unrated."""

    up = "up"
    down = "down"


class ChatSession(SQLModel, table=True):
    """Not `Session`: collides with SQLAlchemy."""

    __tablename__ = "sessions"
    __table_args__ = (
        # sidebar: user's sessions, newest first
        Index("ix_sessions_user_updated", "user_id", "updated_at"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    kind: SessionKind = Field(default=SessionKind.chat)
    title: str = Field(default="")
    project_id: str | None = Field(default=None)
    agent_id: str | None = Field(default=None)
    model: str = Field(default="")
    #: separate mode so a virtual picker entry never reaches LiteLLM
    routing_mode: RoutingMode = Field(
        default=RoutingMode.manual,
        sa_column=Column(String, nullable=False, default=RoutingMode.manual.value),
    )
    artifact_id: str | None = Field(default=None)
    #: not a FK: catalogue ships in image; unknown id → no template
    render_template_id: str | None = Field(default=None)
    #: user pipeline (「이 워크플로로 시작」); else agent binding
    pipeline_id: str | None = Field(default=None)
    #: per-conversation index collection; unguessable (session id is in URLs)
    index_key: str | None = Field(default=None)
    #: paused generation awaiting answers/outline; at most one
    pending: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: `{"through", "text", "turns"}` standing in for turns past the window
    summary: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    skill_ids: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: cross-replica turn lock: claimed before storing the question, ignored once stale
    running_turn: str | None = Field(default=None)
    running_since: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    pinned: bool = Field(default=False)
    #: published-bot conversation: publisher-owned, excluded from owner lists (`owned_by`)
    publication_id: str | None = Field(default=None, index=True)
    visitor_id: str | None = Field(default=None, index=True)
    publication_version_id: str | None = Field(default=None)
    #: sent to the bot's maker (👎 or 「만든 사람에게 보내기」)
    shared_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    #: started under 「대화 열람」 with visitor consent
    viewable: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, default=False,
                                        server_default=false())
    )
    #: user-set title; never auto-replaced
    title_set_by_user: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, default=False,
                                        server_default=false())
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


def owned_by(user_id: str):
    """Own conversations, excluding published-bot rows."""
    return (ChatSession.user_id == user_id) & col(ChatSession.publication_id).is_(None)


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    role: Role
    content: str = Field(default="")

    # JSONB: read/written whole, never queried across rows
    steps: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    attachments: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    usage: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    variants: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: replaced answers on rerun, oldest first; conversation continues from this row
    superseded: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: requested/effective model and privacy action; no prompt text, safe to return
    routing: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: ids only (artifacts versioned/deleted separately)
    artifact_ids: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: retired 시작점; read only
    started_from: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    model: str | None = Field(default=None)

    #: plain string, not a DB enum
    rating: MessageRating | None = Field(default=None, sa_column=Column(String, nullable=True))

    #: assistant row: partial kept; user row: nothing written. Null: normal or legacy.
    failure: TurnFailure | None = Field(default=None, sa_column=Column(String, nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


class PipelineRun(SQLModel, table=True):
    """Pipeline stage records and per-stage snapshots for resume/rerun; kept out of transcripts."""

    __tablename__ = "pipeline_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True, ondelete="CASCADE")
    user_id: str = Field(foreign_key="users.id", index=True)
    message_id: str | None = Field(default=None)
    artifact_id: str | None = Field(default=None)
    artifact_version: int | None = Field(default=None)
    #: rerun source (or answered waiting run) and stage
    parent_id: str | None = Field(default=None)
    from_stage: str | None = Field(default=None)
    pipeline: str = Field(default="")
    flow: str = Field(default="")
    status: str = Field(default="running")
    params: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    stages: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: start values plus changed fields after each stage (`app.pipeline.runs`)
    snapshots: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


__all__ = [
    "ChatSession",
    "owned_by",
    "Message",
    "MessageRating",
    "PipelineRun",
    "Role",
    "RoutingMode",
    "SessionKind",
    "TurnFailure",
]


#: titled after their document, not first words
DOCUMENT_SESSION_KINDS = frozenset({SessionKind.report, SessionKind.slides})


def follow_document_title(session: ChatSession, title: str) -> bool:
    """Title report/slides session after its document unless user-renamed; True if changed."""
    title = " ".join((title or "").split())[:200]
    if (
        not title
        or session.kind not in DOCUMENT_SESSION_KINDS
        or session.title_set_by_user
        or session.title == title
    ):
        return False
    session.title = title
    return True
