"""Web push: one row per subscribed device, and a short log of sent notices."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Text
from sqlmodel import Field, SQLModel

from app.models.user import utcnow

#: notice events → on by default; the settings screen lists them in this order
PUSH_EVENTS: dict[str, bool] = {
    "job_done": True,
    "job_failed": True,
    "shared": True,
    "review": True,
    "decided": True,
    "issue": True,
    "credits": True,
}


def _uuid() -> str:
    return uuid.uuid4().hex


def _ts(**kwargs) -> Column:
    return Column(DateTime(timezone=True), **kwargs)


class PushSubscription(SQLModel, table=True):
    """A browser's PushSubscription; the endpoint is the device's capability URL."""

    __tablename__ = "push_subscriptions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    #: push-service URL; unique, a re-subscribe on the same device replaces the row
    endpoint: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    #: base64url P-256 public key and auth secret from the browser
    p256dh: str
    auth: str
    #: short device label shown in settings (e.g. 「Chrome · Windows」)
    device: str = Field(default="", max_length=80)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    last_sent_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))


class PushSent(SQLModel, table=True):
    """One notice per row, shared by every replica: per-user rate limit and one-time notices."""

    __tablename__ = "push_sent"
    __table_args__ = (
        Index("ix_push_sent_user_at", "user_id", "at"),
        Index("uq_push_sent_once", "user_id", "once_key", unique=True),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id")
    event: str = Field(max_length=24)
    #: set for a notice sent at most once (e.g. one credit warning per cycle)
    once_key: str | None = Field(default=None, max_length=120)
    at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


__all__ = ["PUSH_EVENTS", "PushSent", "PushSubscription"]
