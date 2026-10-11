"""Identity, refresh tokens, credit ledger; `litellm_key` encrypted, never sent to a browser."""

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
    """Timezone-aware; cycle math independent of server zone."""
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

    # allowance; usage resets at cycle_resets_at, no carry-over (no balance column)
    monthly_credits: int = Field(default=0)
    credits_used: int = Field(default=0)
    cycle_resets_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))

    litellm_user_id: str | None = Field(default=None)
    #: Fernet ciphertext; read via `services.litellm.user_key`
    litellm_key: str | None = Field(default=None)
    litellm_key_preview: str | None = Field(default=None)
    litellm_key_issued_at: datetime | None = Field(
        default=None, sa_column=_ts_column(nullable=True)
    )
    avatar_color: str = Field(default="#5b53e8")
    allowed_models: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: defaults in `schemas.auth.Preferences`; missing key = not chosen
    preferences: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )

    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    last_active_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    #: mailed-link confirmation time; null = outstanding
    email_verified_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))

    @property
    def credits_remaining(self) -> int:
        return max(0, self.monthly_credits - self.credits_used)


class ApiKey(SQLModel, table=True):
    """User-held proxy key; full value returned once, then `preview` only."""

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
    """Refresh-token revocation cause; only a returning `rotated` implies replay."""

    rotated = "rotated"
    logout = "logout"
    suspended = "suspended"
    reuse = "reuse"


class RefreshToken(SQLModel, table=True):
    """Issued refresh token, append-only; reuse after rotation revokes the `family_id`."""

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
    #: sign-in origin, copied across the family
    ip: str = Field(default="")
    user_agent: str = Field(default="", max_length=400)
    #: last rotation = still open, not in use (timer refresh)
    last_used_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))

    def is_usable(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.revoked_at is None and self.expires_at > now


class CreditLedger(SQLModel, table=True):
    """Append-only deductions and refills."""

    __tablename__ = "credit_ledger"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    # negative spend, positive grant/refill
    delta: int
    reason: str
    session_id: str | None = Field(default=None)
    job_id: str | None = Field(default=None)
    #: billed model (may differ from session's); null if shared charge
    model: str | None = Field(default=None)
    #: on the row so session deletion doesn't move spend
    surface: str | None = Field(default=None)
    #: work in `unit`; lets zero-credit models reach usage screens
    units: int | None = Field(default=None)
    unit: str | None = Field(default=None)
    #: published-bot turn: billed to owner, shown separately
    publication_id: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


class AuditEvent(SQLModel, table=True):
    """Institution-facing audit trail."""

    __tablename__ = "audit_events"

    id: str = Field(default_factory=_uuid, primary_key=True)
    at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))
    actor_id: str | None = Field(default=None, index=True)
    action: str
    target: str = Field(default="")
    detail: str = Field(default="")
    #: value-free structured context; Python name avoids reserved `metadata`
    event_metadata: dict | None = Field(
        default=None, sa_column=Column("metadata", JSONB, nullable=True)
    )
    ip: str = Field(default="")
    #: raw request `User-Agent`; empty without request
    user_agent: str = Field(default="")
    severity: str = Field(default="info")


class PasswordReset(SQLModel, table=True):
    """Single-use reset ticket; hash only, kept after use to detect second clicks."""

    __tablename__ = "password_resets"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime = Field(sa_column=_ts_column(nullable=False))
    used_at: datetime | None = Field(default=None, sa_column=_ts_column(nullable=True))
    requested_ip: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts_column(nullable=False))


class EmailVerification(SQLModel, table=True):
    """Single-use email verification ticket; same rules as `PasswordReset`."""

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
