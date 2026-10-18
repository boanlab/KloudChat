"""공개 챗봇: frozen agent copy, publisher-owned sessions, admin-gated outside."""

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
    internal = "internal"
    #: link holders, unlisted (admin approval)
    link = "link"
    #: anyone, publicly listed (admin approval)
    external = "external"


#: narrowing is the owner's; widening is reviewed
VISIBILITY_RANK = {"internal": 0, "link": 1, "external": 2}


class ReviewState(StrEnum):
    """Owner's pending request for wider audience or new version."""

    none = ""
    pending = "pending"
    rejected = "rejected"


class PublicationStatus(StrEnum):
    draft = "draft"
    #: awaiting admin (link/external)
    pending = "pending"
    approved = "approved"
    #: by owner, admin, or automatically (credits, account)
    paused = "paused"
    rejected = "rejected"
    revoked = "revoked"


class Publication(SQLModel, table=True):
    __tablename__ = "publications"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    #: not a FK: versions are copies, survive edits/deletion
    agent_id: str = Field(default="", index=True)
    #: `/b/<slug>`
    slug: str = Field(index=True, unique=True)
    title: str = Field(default="")
    description: str = Field(default="")
    visibility: str = Field(default=PublicationVisibility.internal.value, max_length=20)
    status: str = Field(default=PublicationStatus.draft.value, max_length=20)
    #: `owner`, `admin`, `credits`, `account`, `model`; else empty
    pause_reason: str = Field(default="", max_length=40)
    active_version_id: str | None = Field(default=None)
    branding: dict | None = Field(default=None, sa_column=_json(nullable=True))
    limits: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: reserved; unknown keys ignored
    features: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: visitor-visible working detail: `none` · `summary` · `full`
    show_process: str = Field(default="summary", max_length=20)
    show_sources: bool = Field(default=True)
    #: 「복사 허용」: members may copy the bot (instructions included) as their own agent
    allow_copy: bool = Field(default=False)
    retention_days: int = Field(default=30)
    #: `off`: stats and sent conversations only; `on` (「대화 열람」): all after consent
    conversation_view: str = Field(default="off", max_length=8)
    #: bumped on notice change; consent is per version
    consent_version: int = Field(default=1)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    published_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: audience under (or refused) review; else ""
    requested_visibility: str = Field(default="", max_length=20)
    review_state: str = Field(default=ReviewState.none.value, max_length=20)
    #: admin's note on last decision, shown to owner
    review_note: str = Field(default="")
    reviewed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    reviewed_by: str | None = Field(default=None)
    #: version awaiting review; approved one answers meanwhile
    pending_version_id: str | None = Field(default=None)
    #: owner badge: attention_at later than owner_seen_at
    attention_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    owner_seen_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: admin-opened channels; key present = open
    channels: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: channels requested under review; else None
    requested_channels: dict | None = Field(default=None, sa_column=_json(nullable=True))


class PublicationVersion(SQLModel, table=True):
    """Frozen bot state: hidden agent copy, model, contents."""

    __tablename__ = "publication_versions"
    __table_args__ = (UniqueConstraint("publication_id", "number",
                                       name="ux_publication_versions_number"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", index=True, ondelete="CASCADE")
    number: int = Field(default=1)
    #: hidden `agents` row (`Agent.snapshot_of`)
    agent_snapshot_id: str = Field(default="")
    #: strict-local answer model
    model: str = Field(default="")
    note: str = Field(default="")
    #: version-list summary of what was fixed
    summary: dict | None = Field(default=None, sa_column=_json(nullable=True))
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    #: admin approval for account-less audiences
    approved_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    approved_by: str | None = Field(default=None)


class PublicationVisitor(SQLModel, table=True):
    """Bot visitor: consent and block state."""

    __tablename__ = "publication_visitors"
    __table_args__ = (UniqueConstraint("publication_id", "user_id",
                                       name="ux_publication_visitors_user"),
                      Index("ux_publication_visitors_anon", "publication_id", "anon_id",
                            unique=True))

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", index=True, ondelete="CASCADE")
    #: member; null if account-less
    user_id: str | None = Field(default=None, foreign_key="users.id", index=True,
                                ondelete="CASCADE")
    #: account-less id from signed cookie
    anon_id: str | None = Field(default=None, max_length=64)
    #: consented notice version; 0 = none
    consent_version: int = Field(default=0)
    consented_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    blocked: bool = Field(default=False)
    blocked_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    first_seen_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    last_seen_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class PublicationCounter(SQLModel, table=True):
    """Windowed count (`m:…`, `d:…`); upsert-added so replicas don't overwrite."""

    __tablename__ = "publication_counters"
    publication_id: str = Field(foreign_key="publications.id", primary_key=True,
                                ondelete="CASCADE")
    window: str = Field(primary_key=True, max_length=20)
    key: str = Field(primary_key=True, max_length=120)
    value: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0))


class PublicationSlot(SQLModel, table=True):
    """Running bot turn for cross-replica concurrency limits; stale past claim TTL."""

    __tablename__ = "publication_slots"
    __table_args__ = (Index("ix_publication_slots_acquired", "publication_id", "acquired_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", ondelete="CASCADE")
    acquired_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class PublicationQuestion(SQLModel, table=True):
    """Unanswerable question, PII-masked; owner sees only this sentence."""

    __tablename__ = "publication_questions"
    __table_args__ = (Index("ix_publication_questions_at", "publication_id", "at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", ondelete="CASCADE")
    version_id: str | None = Field(default=None)
    text: str = Field(default="")
    at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class PublicationKey(SQLModel, table=True):
    """Per-bot API/MCP key; hash only, shown once."""

    __tablename__ = "publication_keys"

    id: str = Field(default_factory=_uuid, primary_key=True)
    publication_id: str = Field(foreign_key="publications.id", index=True, ondelete="CASCADE")
    name: str = Field(default="", max_length=80)
    key_hash: str = Field(max_length=64, unique=True, index=True)
    #: `kcb_ab12…yz89` for telling keys apart
    preview: str = Field(default="", max_length=40)
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    last_used_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    revoked_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))


__all__ = [
    "Publication",
    "PublicationCounter",
    "PublicationKey",
    "PublicationQuestion",
    "PublicationSlot",
    "PublicationStatus",
    "PublicationVersion",
    "PublicationVisibility",
    "PublicationVisitor",
    "ReviewState",
    "VISIBILITY_RANK",
]
