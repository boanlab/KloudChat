"""Google and Slack integrations: a person's connected account, the writes waiting for
their confirmation, Drive files brought in as material, and the workspaces the Slack app is
installed in. Tokens are sealed with the settings key (`settings_store.encrypt_secret`)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


def _uuid() -> str:
    return uuid.uuid4().hex


def _ts(**kwargs) -> Column:
    return Column(DateTime(timezone=True), **kwargs)


def _json(**kwargs) -> Column:
    return Column(JSONB, **kwargs)


class Provider(StrEnum):
    google = "google"
    slack = "slack"


class AccountStatus(StrEnum):
    #: authorization started, not finished
    pending = "pending"
    connected = "connected"
    #: the provider refused the stored token; the person connects again
    needs_auth = "needs_auth"


class IntegrationAccount(SQLModel, table=True):
    """One person's connection to one provider; never sent to the browser whole."""

    __tablename__ = "integration_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="ux_integration_accounts"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    provider: str = Field(max_length=20)
    status: str = Field(default=AccountStatus.pending.value, max_length=20)
    #: shown on the settings screen: the Google address, or `workspace · @name`
    label: str = Field(default="", max_length=200)
    #: Google `sub` or Slack user id
    external_id: str = Field(default="", max_length=120)
    #: Slack workspace id
    team_id: str = Field(default="", max_length=40)
    #: scopes the provider granted (a person may grant fewer than asked)
    scopes: list | None = Field(default=None, sa_column=_json(nullable=True))
    access_token: str = Field(default="")
    refresh_token: str = Field(default="")
    expires_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: SHA-256 of the authorization `state`; the PKCE verifier sealed beside it
    pending_state: str = Field(default="", max_length=64, index=True)
    pending_verifier: str = Field(default="")
    pending_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: SHA-256 of the nonce in the starting browser's cookie: only that browser finishes
    pending_browser: str = Field(default="", max_length=64)
    #: per-person choices, e.g. Slack `notify: {"channel": "C1", "name": "#팀"}`
    prefs: dict | None = Field(default=None, sa_column=_json(nullable=True))
    last_used_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    error: str = Field(default="", max_length=300)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class ActionStatus(StrEnum):
    pending = "pending"
    done = "done"
    cancelled = "cancelled"
    failed = "failed"


class IntegrationAction(SQLModel, table=True):
    """A write the agent proposed (an event, a Drive file, a Slack message): nothing reaches
    the provider until the person confirms it on the card."""

    __tablename__ = "integration_actions"
    __table_args__ = (Index("ix_integration_actions_user_created", "user_id", "created_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", ondelete="CASCADE")
    session_id: str | None = Field(default=None, max_length=32)
    provider: str = Field(max_length=20)
    #: `calendar.create_event`, `calendar.update_event`, `drive.export`, `slack.post_message`
    kind: str = Field(max_length=40)
    #: the checked arguments the write will send
    payload: dict | None = Field(default=None, sa_column=_json(nullable=True))
    status: str = Field(default=ActionStatus.pending.value, max_length=20)
    #: what the write made: `{"id", "url"}`
    result: dict | None = Field(default=None, sa_column=_json(nullable=True))
    error: str = Field(default="", max_length=300)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    decided_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))


class IntegrationImport(SQLModel, table=True):
    """A Drive file copied in as a stored file (project material or an attachment), kept so
    it can be synced again from Drive."""

    __tablename__ = "integration_imports"
    __table_args__ = (Index("ix_integration_imports_user_external", "user_id", "external_id"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", ondelete="CASCADE")
    file_id: str = Field(index=True, max_length=32)
    project_id: str | None = Field(default=None, max_length=32)
    provider: str = Field(default=Provider.google.value, max_length=20)
    external_id: str = Field(max_length=200)
    #: Drive mime type and `modifiedTime` at the last sync
    mime: str = Field(default="", max_length=200)
    modified: str = Field(default="", max_length=40)
    source_url: str = Field(default="", max_length=500)
    synced_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class SlackWorkspace(SQLModel, table=True):
    """A Slack workspace where the app (its bot) is installed: by whoever connected first
    there, when the workspace lets members install apps, or by its Slack admin."""

    __tablename__ = "slack_workspaces"

    team_id: str = Field(primary_key=True, max_length=40)
    name: str = Field(default="", max_length=200)
    #: sealed bot token
    bot_token: str = Field(default="")
    bot_user_id: str = Field(default="", max_length=40)
    scopes: list | None = Field(default=None, sa_column=_json(nullable=True))
    installed_by: str | None = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


__all__ = [
    "AccountStatus",
    "ActionStatus",
    "IntegrationAccount",
    "IntegrationAction",
    "IntegrationImport",
    "Provider",
    "SlackWorkspace",
]
