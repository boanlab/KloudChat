"""Auth wire shapes: camelCase on the wire (alias generator), snake_case in Python.
Mirrored by the web client's types."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import AuditEvent, User, UserRole, UserStatus
from app.services import geoip


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


class Wire(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class SignupRequest(Wire):
    email: EmailStr
    # Upper bound keeps argon2 from becoming a DoS vector.
    password: str = Field(min_length=10, max_length=200)
    name: str = Field(min_length=1, max_length=80)


class LoginRequest(Wire):
    email: EmailStr
    password: str = Field(max_length=200)


class Preferences(Wire):
    """Settings-screen switches. Every field has a default for accounts with nothing stored."""

    #: Off: the answer appears in one piece when the turn ends.
    stream_responses: bool = True
    #: Extract durable facts from finished turns into memory.
    auto_memory: bool = False
    #: The model / token / credit line under each answer.
    show_usage: bool = True
    #: Default action when protected data would leave for an external model;
    #: the administrator's policy is the upper bound for raw delivery.
    privacy_default_action: Literal[
        "ask", "route_strict_local", "mask_external", "send_raw_external"
    ] = "ask"
    #: Personalisation: what every conversation should know about the person,
    #: and how answers should be written. Free text.
    about_me: str = Field(default="", max_length=1500)
    response_style: str = Field(default="", max_length=1500)

    @classmethod
    def of(cls, user: User) -> Preferences:
        """Stored values over defaults; accepts both field and alias spellings."""
        stored = user.preferences or {}
        known = set(cls.model_fields) | {f.alias for f in cls.model_fields.values() if f.alias}
        return cls(**{k: v for k, v in stored.items() if k in known})


class ProfilePatch(Wire):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_color: str | None = Field(default=None, max_length=16)
    preferences: Preferences | None = None


class PasswordChange(Wire):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class PasswordForgot(Wire):
    email: EmailStr


class PasswordReset(Wire):
    token: str = Field(max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class EmailVerify(Wire):
    token: str = Field(min_length=16, max_length=256)


class UserOut(Wire):
    id: str
    email: str
    name: str
    role: UserRole
    status: UserStatus
    monthly_credits: int
    credits_used: int
    cycle_resets_at: datetime | None
    avatar_color: str
    created_at: datetime
    last_active_at: datetime | None
    #: Null while a mailed verification link is still out.
    email_verified_at: datetime | None = None
    #: Last four characters of the account's LiteLLM key; no route returns the key.
    litellm_key_preview: str | None = None
    litellm_key_issued_at: datetime | None = None
    preferences: Preferences = Preferences()
    #: Empty means the whole catalogue.
    allowed_models: list[str] = []

    @classmethod
    def of(cls, user: User) -> UserOut:
        out = cls.model_validate(user, from_attributes=True)
        out.preferences = Preferences.of(user)
        out.allowed_models = list(user.allowed_models or [])
        return out


class SessionOut(Wire):
    """Login/refresh result. The refresh token travels as an httpOnly cookie, not here;
    `expiresIn` schedules the SPA's silent refresh."""

    access_token: str
    expires_in: int
    user: UserOut


class SignupResponse(Wire):
    """`session` is null when approval is required (`status == "pending"`)."""

    user: UserOut
    session: SessionOut | None = None


class EmailVerifyResponse(Wire):
    """`active` with a session when verifying was the last step; `pending` when
    an administrator still has to approve."""

    status: UserStatus
    session: SessionOut | None = None


class AccessEventOut(Wire):
    """One line of the user's own access log.

    `action` is the stored verb; the client localises it. `region` is empty
    without a GeoLite2 database or for an uncovered address, never guessed.
    """

    id: str
    at: datetime
    action: str
    detail: str
    ip: str
    region: str
    user_agent: str
    severity: str

    @classmethod
    def of(cls, e: AuditEvent) -> AccessEventOut:
        return cls(
            id=e.id,
            at=e.at,
            action=e.action,
            detail=e.detail,
            ip=e.ip,
            region=geoip.lookup(e.ip),
            user_agent=e.user_agent,
            severity=e.severity,
        )


class ActiveSessionOut(Wire):
    """One live sign-in, keyed on the refresh-token family (tokens rotate; the
    family is the session). `current` marks the caller's own; ending it signs
    the caller out. `ip`/`region`/`userAgent` may be empty, never guessed.
    """

    family_id: str
    started_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str
    region: str
    user_agent: str
    current: bool


class SessionRevokeResult(Wire):
    """How many sign-ins the revoke ended."""

    revoked: int
