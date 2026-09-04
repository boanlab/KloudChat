"""Identity, refresh tokens, and the credit ledger.

`litellm_user_id` is a one-way provisioning pointer set at activation; null
without LiteLLM. `litellm_key` is the user's own proxy key, used for every model
call on their behalf; encrypted at rest and never sent to a browser.
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
    """Timezone-aware column; cycle math must not depend on the server's zone."""
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
    #: Last four characters, for the admin screen.
    litellm_key_preview: str | None = Field(default=None)
    litellm_key_issued_at: datetime | None = Field(
        default=None, sa_column=_ts_column(nullable=True)
    )
    avatar_color: str = Field(default="#5b53e8")
    allowed_models: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: Settings-screen switches. Defaults live in `schemas.auth.Preferences`;
    #: a missing key means "not chosen".
    preferences: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )

    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    last_active_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    #: Confirmation time of the mailed link (creation time for accounts never
    #: asked). Null: link still outstanding.
    email_verified_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))

    @property
    def credits_remaining(self) -> int:
        return max(0, self.monthly_credits - self.credits_used)


class ApiKey(SQLModel, table=True):
    """A proxy key the user holds themselves (unlike `User.litellm_key`).

    Returned in full once, at creation; only `preview` afterwards.
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
    """Why a refresh token stopped being valid. Only a returning `rotated` token implies replay."""

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
    #: Sign-in origin, copied onto every token of the family for the session list.
    ip: str = Field(default="")
    user_agent: str = Field(default="", max_length=400)
    #: Last rotation; means "still open", not "in use" (the browser refreshes on a timer).
    last_used_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))

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
    #: Model billed, recorded on the row because it can differ from the session's
    #: model. Null when several models shared one charge.
    model: str | None = Field(default=None)
    #: Surface the charge came from (`chat`, `image`, `av`, ...), kept on the row
    #: so deleting the session does not move its spend.
    surface: str | None = Field(default=None)
    #: Work done, in `unit` (seconds transcribed, chunks embedded). Set for
    #: zero-credit models so they still reach the usage screens.
    units: int | None = Field(default=None)
    unit: str | None = Field(default=None)
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
    #: Structured, value-free context for machine-readable security events.
    #: The Python name avoids SQLAlchemy's reserved ``metadata`` attribute.
    event_metadata: dict | None = Field(
        default=None, sa_column=Column("metadata", JSONB, nullable=True)
    )
    ip: str = Field(default="")
    #: Raw `User-Agent` of the causing request; empty when there is no request.
    user_agent: str = Field(default="")
    severity: str = Field(default="info")


class PasswordReset(SQLModel, table=True):
    """Single-use password reset ticket. Only the hash is stored; rows are kept
    after use so `used_at` tells a second click apart from a bad link."""

    __tablename__ = "password_resets"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime = Field(sa_column=_ts_column(nullable=False))
    used_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    requested_ip: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


class EmailVerification(SQLModel, table=True):
    """Single-use email verification ticket; same shape and rules as `PasswordReset`."""

    __tablename__ = "email_verifications"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime = Field(sa_column=_ts_column(nullable=False))
    used_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


__all__ = [
    "EmailVerification",
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
