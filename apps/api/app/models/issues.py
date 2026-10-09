"""Issue reports (「문의·신고」): what a person reported, the session snapshot they agreed to
share, and the thread between them and the administrators."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


def _uuid() -> str:
    return uuid.uuid4().hex


def _ts(**kwargs) -> Column:
    return Column(DateTime(timezone=True), **kwargs)


class IssueKind(StrEnum):
    bug = "bug"
    feature = "feature"
    other = "other"


class IssueStatus(StrEnum):
    #: 접수 → 확인 중 → 해결 / 보류 (with a reason).
    received = "received"
    checking = "checking"
    resolved = "resolved"
    on_hold = "on_hold"


class IssueReport(SQLModel, table=True):
    __tablename__ = "issue_reports"
    __table_args__ = (Index("ix_issue_reports_status_created", "status", "created_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    #: Goes with its reporter's account.
    user_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    kind: str = Field(default=IssueKind.bug.value)
    status: str = Field(default=IssueStatus.received.value)
    body: str = Field(default="")
    #: An administrator; null until one takes it.
    assignee_id: str | None = Field(default=None)
    hold_reason: str = Field(default="")
    #: The session it was sent from and the item it points at. References only: the
    #: content, when shared, is in `snapshot`.
    session_id: str | None = Field(default=None)
    message_id: str | None = Field(default=None)
    artifact_id: str | None = Field(default=None)
    #: The reporter ticked 「이 세션을 관리자에게 공유합니다」; `snapshot` is null otherwise.
    session_shared: bool = Field(default=False)
    #: App build, browser, session kind, models, agent, pipeline, stage records (no
    #: evidence), artifact version ids. No message text.
    context: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    #: Read-only copy of the session at submit time; see `services.issue_snapshot`.
    snapshot: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    snapshot_bytes: int = Field(default=0)
    #: Copied screenshot blob, under the reporter's storage directory.
    screenshot_key: str | None = Field(default=None)
    screenshot_name: str = Field(default="")
    screenshot_mime: str = Field(default="")
    #: Last reply or status change by an administrator, and when the reporter last read
    #: the report: the reply badge is the reports where the first is later.
    admin_updated_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    user_seen_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class IssueReply(SQLModel, table=True):
    """One entry in a report's thread: a reply, or a status change (with `status` set)."""

    __tablename__ = "issue_replies"

    id: str = Field(default_factory=_uuid, primary_key=True)
    report_id: str = Field(foreign_key="issue_reports.id", index=True, ondelete="CASCADE")
    author_id: str | None = Field(default=None)
    #: `admin` or `user`; copied so the thread reads right after a role change.
    author_role: str = Field(default="user")
    author_name: str = Field(default="")
    body: str = Field(default="")
    status: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


__all__ = ["IssueKind", "IssueReply", "IssueReport", "IssueStatus"]
