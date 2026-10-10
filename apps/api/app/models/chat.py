"""Sessions and messages. One `sessions` table carries every surface, discriminated by `kind`."""

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
    #: A user pipeline this conversation runs (「이 워크플로로 시작」); an agent's binding
    #: applies when this is unset.
    pipeline_id: str | None = Field(default=None)
    #: Retrieval-index collection for files uploaded into this conversation, minted on
    #: the first indexed upload. Unguessable on purpose: the session id is in URLs.
    index_key: str | None = Field(default=None)
    #: A generation paused to ask something: its request, attachments, and the
    #: questions or outline it is waiting on. At most one per session.
    pending: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: What stands in for the turns too old to send: `{"through": <message id>,
    #: "text": ..., "turns": n}`, extended with the turns dropped since. Null until the
    #: conversation outgrows the model's window.
    summary: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: Skills switched on for this conversation. A skill picked for one turn stays on
    #: until the person takes it off (session PATCH) or picks a different set.
    skill_ids: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: The chat turn running on this session, across replicas: a token claimed with a
    #: conditional update before the question is stored, released when the answer is
    #: settled or stop is pressed, and ignored once stale.
    running_turn: str | None = Field(default=None)
    running_since: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    pinned: bool = Field(default=False)
    #: A conversation on a published bot (`publications`): owned by the publisher, opened by
    #: a visitor. Kept out of the owner's own lists, search and transcript routes
    #: (`owned_by`); only the visitor reads it, and the owner only what the visibility rules
    #: allow (`services.publishing`).
    publication_id: str | None = Field(default=None, index=True)
    #: The visitor (`publication_visitors.id`) and the version the conversation runs on.
    visitor_id: str | None = Field(default=None, index=True)
    publication_version_id: str | None = Field(default=None)
    #: The visitor sent this conversation to the bot's maker (👎 or 「만든 사람에게 보내기」).
    shared_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    #: Started while 「대화 열람」 was on, after the visitor's consent.
    viewable: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, default=False,
                                        server_default=false())
    )
    #: The person named this conversation themselves; no automatic title replaces theirs.
    title_set_by_user: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, default=False,
                                        server_default=false())
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


def owned_by(user_id: str):
    """The person's own conversations: theirs, and not a published bot's (whose rows the
    publisher owns but never lists)."""
    return (ChatSession.user_id == user_id) & col(ChatSession.publication_id).is_(None)


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
    #: Answers this one replaced when the question was run again, oldest first:
    #: `{"id", "content", "model", "usage", "createdAt"}`. The transcript lets the
    #: reader page back through them; the conversation continues from this row.
    superseded: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: Requested/effective model and privacy action. Contains no prompt text or
    #: detected value, and is safe to return with the transcript.
    routing: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: Artifacts this turn produced, rendered in place of a text answer. Ids
    #: only: artifacts are versioned and deleted on their own.
    artifact_ids: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    #: Legacy: the retired 시작점 an old turn began from, `{"templateId", "title"}`.
    #: No longer written; kept so old transcripts still name it.
    started_from: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    model: str | None = Field(default=None)

    #: Owner's verdict on the answer. Plain string column, not a database enum.
    rating: MessageRating | None = Field(default=None, sa_column=Column(String, nullable=True))

    #: On the assistant row when a partial reply was kept, on the user row when
    #: nothing was written. Null: answered normally, or a row older than the
    #: column (those are read positionally as a question with nothing under it).
    failure: TurnFailure | None = Field(default=None, sa_column=Column(String, nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


class PipelineRun(SQLModel, table=True):
    """One pass of a pipeline over a turn: its stage records and, after each stage, what
    that stage changed on the work bundle — so the run can be resumed or run again from any
    recorded stage in a later request.

    Kept apart from `messages`: the snapshots are the turn's working state (material,
    sections at every step) and are read only by a rerun, never with the transcript."""

    __tablename__ = "pipeline_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    #: Goes with its session (the database cascades a session's delete).
    session_id: str = Field(foreign_key="sessions.id", index=True, ondelete="CASCADE")
    user_id: str = Field(foreign_key="users.id", index=True)
    #: The assistant message that settled it, and the artifact (and version) it wrote.
    message_id: str | None = Field(default=None)
    artifact_id: str | None = Field(default=None)
    artifact_version: int | None = Field(default=None)
    #: The run this one reran (or the waiting run it answered), and from which stage.
    parent_id: str | None = Field(default=None)
    from_stage: str | None = Field(default=None)
    #: `app/pipelines/*.toml` name and flow (`write`, `revise`, `pre` …).
    pipeline: str = Field(default="")
    flow: str = Field(default="")
    #: running · done · stopped · failed · waiting · cancelled
    status: str = Field(default="running")
    #: What the turn ran with, beyond the bundle (model, template, project, routing …).
    params: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: `StepRecord.as_dict` per stage, in order.
    stages: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: `{"start": {field: value}, "after": [{"stage", "fields": {field: value}}]}`, encoded
    #: by `app.pipeline.runs` (changed fields only after each stage).
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


#: Conversations named after the document they made rather than their first words.
DOCUMENT_SESSION_KINDS = frozenset({SessionKind.report, SessionKind.slides})


def follow_document_title(session: ChatSession, title: str) -> bool:
    """Names a report or slides conversation after its document; True when the title changed.

    A conversation the person renamed keeps their name, and a chat keeps its own title.
    """
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
