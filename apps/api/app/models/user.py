"""Identity, refresh tokens, and the credit ledger.

`User` is a kchat row, not a mirror of a LiteLLM user. `litellm_user_id` is a
one-way provisioning pointer, written when an account is activated. With
LiteLLM absent it stays null and everything except model calls still works.

`litellm_key` is that user's virtual key on the proxy, and the credential every
model call for them is made with — so spend, rate limits and audit trails land
on the person. Encrypted at rest, and never sent to a browser.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ts_column(**kwargs) -> Column:
    """Timestamps are stored with tz so cycle math survives a server in another zone."""
    return Column(DateTime(timezone=True), **kwargs)


class UserRole(StrEnum):
    admin = "admin"
    user = "user"


class UserStatus(StrEnum):
    active = "active"
    pending = "pending"
    suspended = "suspended"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=_uuid, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    name: str
    role: UserRole = Field(default=UserRole.user)
    status: UserStatus = Field(default=UserStatus.pending)

    # ── credits ────────────────────────────────────────────────────────
    # Admin-assigned allowance. `credits_used` resets at `cycle_resets_at` and
    # nothing carries over, so there is no balance column.
    monthly_credits: int = Field(default=0)
    credits_used: int = Field(default=0)
    cycle_resets_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))

    litellm_user_id: str | None = Field(default=None)
    #: Fernet ciphertext. Read it through `services.litellm.user_key`, never raw.
    litellm_key: str | None = Field(default=None)
    #: Last four characters, for the admin screen: enough to match a row in
    #: LiteLLM's own UI, useless to replay.
    litellm_key_preview: str | None = Field(default=None)
    litellm_key_issued_at: datetime | None = Field(
        default=None, sa_column=_ts_column(nullable=True)
    )
    avatar_color: str = Field(default="#5b53e8")
    #: Behaviour switches owned by the settings screen. Defaults live in
    #: `schemas.auth.Preferences`: a missing key means "not chosen", and the
    #: schema decides what unchosen means.
    allowed_models: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    preferences: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )

    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    last_active_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))

    @property
    def credits_remaining(self) -> int:
        return max(0, self.monthly_credits - self.credits_used)


class ApiKey(SQLModel, table=True):
    """A proxy key the user holds themselves.

    Distinct from `User.litellm_key`, which kchat uses on their behalf. Handed
    over once at creation and shown only as a preview afterwards — no route
    returns it again.
    """

    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_user_id", "user_id"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    name: str
    secret: str
    preview: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    last_used_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    revoked_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))


class RevokeReason(StrEnum):
    """Why a refresh token stopped being valid.

    Only a returning `rotated` token implies replay; the rest are ordinary
    session endings. Without the distinction, every logout and suspension would
    raise a "token reuse" alert.
    """

    rotated = "rotated"
    logout = "logout"
    suspended = "suspended"
    reuse = "reuse"


class RefreshToken(SQLModel, table=True):
    """One row per issued refresh token. Rotation appends, never updates.

    `family_id` ties a rotation chain together: a token presented after rotation
    means a leak, so the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_family_id", "family_id"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    family_id: str
    expires_at: datetime = Field(sa_column=_ts_column(nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    revoked_reason: RevokeReason | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))

    def is_usable(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.revoked_at is None and self.expires_at > now


class CreditLedger(SQLModel, table=True):
    """Append-only. Every deduction and every refill lands here for the usage screens."""

    __tablename__ = "credit_ledger"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    # Negative for spend, positive for an allowance grant or refill.
    delta: int
    reason: str
    session_id: str | None = Field(default=None)
    job_id: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


class AuditEvent(SQLModel, table=True):
    """Institution-facing trail. Written by the admin and auth routes."""

    __tablename__ = "audit_events"

    id: str = Field(default_factory=_uuid, primary_key=True)
    at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    actor_id: str | None = Field(default=None, index=True)
    action: str
    target: str = Field(default="")
    detail: str = Field(default="")
    ip: str = Field(default="")
    severity: str = Field(default="info")


class PasswordReset(SQLModel, table=True):
    """A single-use ticket to set a new password without knowing the old one.

    Only the hash is stored: a table of live reset tokens is a table of working
    passwords, and a database dump is the realistic exposure.

    Rows are kept after use — `used_at` is what tells a second click on the same
    link apart from a link that never existed.
    """

    __tablename__ = "password_resets"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime = Field(sa_column=_ts_column(nullable=False))
    used_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    requested_ip: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=_ts_column(nullable=False)
    )


__all__ = [
    "PasswordReset",
    "ApiKey",
    "AuditEvent",
    "CreditLedger",
    "RefreshToken",
    "RevokeReason",
    "User",
    "UserRole",
    "UserStatus",
    "utcnow",
]
