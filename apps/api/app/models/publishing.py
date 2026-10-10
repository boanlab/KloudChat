"""Published agents (공개 챗봇): an agent fixed at one version and opened to other people.

A publication is its owner's: every conversation on it is an ordinary `sessions` row owned
by the publisher (`ChatSession.publication_id`), so the normal turn applies the publisher's
policy, credits and audit attribution. What a visitor reaches is fixed at publish time: a
hidden copy of the agent (`Agent.snapshot_of`) and the text of its knowledge.

Phase 1 opens bots to signed-in members only (`visibility = internal`); `link` and
`external` exist in the schema for the administrator-approved scopes that follow.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Column, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


def _uuid() -> str:
    return uuid.uuid4().hex


def _ts(**kwargs) -> Column:
    return Column(DateTime(timezone=True), **kwargs)


def _json(**kwargs) -> Column:
    return Column(JSONB, **kwargs)


class PublicationVisibility(StrEnum):
    #: Signed-in members of this instance.
    internal = "internal"
    #: Whoever holds the link (administrator approval; not in phase 1).
    link = "link"
    #: Embedded or opened outside the institution (administrator approval; not in phase 1).
    external = "external"


class PublicationStatus(StrEnum):
    draft = "draft"
    #: Waiting for an administrator (link/external scopes).
    pending = "pending"
    #: Open to its audience.
    approved = "approved"
    #: Closed for now: by its owner, by an administrator, or automatically (credits, account).
    paused = "paused"
    rejected = "rejected"
    revoked = "revoked"


#: Who may see what a visitor typed: `off` — usage statistics only (and conversations a
#: visitor chose to send); `on` (「대화 열람」) — every conversation started after consent.
CONVERSATION_VIEW = ("off", "on")
#: How much of the answer's working the visitor sees.
SHOW_PROCESS = ("none", "summary", "full")


class Publication(SQLModel, table=True):
    __tablename__ = "publications"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    #: The agent it was published from. Not a foreign key: the published versions are
    #: copies, so the bot keeps working when the original is edited or deleted.
    agent_id: str = Field(default="", index=True)
    #: The address: `/b/<slug>`.
    slug: str = Field(index=True, unique=True)
    title: str = Field(default="")
    description: str = Field(default="")
    visibility: str = Field(default=PublicationVisibility.internal.value, max_length=20)
    status: str = Field(default=PublicationStatus.draft.value, max_length=20)
    #: Why it is paused: `owner`, `admin`, `credits`, `account`, `model`. Empty otherwise.
    pause_reason: str = Field(default="", max_length=40)
    #: The `publication_versions` row visitors talk to.
    active_version_id: str | None = Field(default=None)
    #: `{"greeting", "suggestedPrompts": [≤4], "placeholder"}`.
    branding: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: Overrides of `services.publishing.DEFAULT_LIMITS`.
    limits: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: Reserved switches (`{"feedback": true, ...}`); unknown keys are ignored.
    features: dict | None = Field(default=None, sa_column=_json(nullable=True))
    show_process: str = Field(default="summary", max_length=20)
    show_sources: bool = Field(default=True)
    #: Days a conversation is kept after its last message.
    retention_days: int = Field(default=30)
    conversation_view: str = Field(default="off", max_length=8)
    #: Raised whenever what the notice says changes; a visitor consents to one version.
    consent_version: int = Field(default=1)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    published_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))


class PublicationVersion(SQLModel, table=True):
    """One fixed state of the bot: the hidden agent copy, the model and what it held."""

    __tablename__ = "publication_versions"
    __table_args__ = (UniqueConstraint("publication_id", "number",
                                       name="ux_publication_versions_number"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", index=True, ondelete="CASCADE")
    number: int = Field(default=1)
    #: The hidden `agents` row (`Agent.snapshot_of`) this version runs.
    agent_snapshot_id: str = Field(default="")
    #: The strict-local model it answers with.
    model: str = Field(default="")
    note: str = Field(default="")
    #: What was fixed, for the version list: `{"knowledge": [{"name", "chars"}], "tools",
    #: "checks", "agentName"}`.
    summary: dict | None = Field(default=None, sa_column=_json(nullable=True))
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class PublicationVisitor(SQLModel, table=True):
    """A person who opened a bot: their consent, and whether the owner blocked them."""

    __tablename__ = "publication_visitors"
    __table_args__ = (UniqueConstraint("publication_id", "user_id",
                                       name="ux_publication_visitors_user"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", index=True, ondelete="CASCADE")
    #: A member; null is reserved for the link scope's anonymous visitor.
    user_id: str | None = Field(default=None, foreign_key="users.id", index=True,
                                ondelete="CASCADE")
    #: The notice version consented to; 0 is none yet.
    consent_version: int = Field(default=0)
    consented_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    blocked: bool = Field(default=False)
    first_seen_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    last_seen_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class PublicationCounter(SQLModel, table=True):
    """A count in one time window (`m:202610101230`, `d:20261010`): turns, tokens, ratings,
    topics. Written with one upsert per change, so replicas add up instead of overwriting."""

    __tablename__ = "publication_counters"
    publication_id: str = Field(foreign_key="publications.id", primary_key=True,
                                ondelete="CASCADE")
    window: str = Field(primary_key=True, max_length=20)
    key: str = Field(primary_key=True, max_length=120)
    value: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0))


class PublicationSlot(SQLModel, table=True):
    """A turn running on a bot, held while it streams: the concurrency limits count these
    rows across replicas. A row older than the claim TTL is a crash's leftover."""

    __tablename__ = "publication_slots"
    __table_args__ = (Index("ix_publication_slots_acquired", "publication_id", "acquired_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", ondelete="CASCADE")
    acquired_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class PublicationQuestion(SQLModel, table=True):
    """A question the bot could not answer from its material, personal details masked. The
    owner sees these sentences, never the conversation around them."""

    __tablename__ = "publication_questions"
    __table_args__ = (Index("ix_publication_questions_at", "publication_id", "at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", ondelete="CASCADE")
    version_id: str | None = Field(default=None)
    text: str = Field(default="")
    at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


__all__ = [
    "CONVERSATION_VIEW",
    "SHOW_PROCESS",
    "Publication",
    "PublicationCounter",
    "PublicationQuestion",
    "PublicationSlot",
    "PublicationStatus",
    "PublicationVersion",
    "PublicationVisibility",
    "PublicationVisitor",
]
