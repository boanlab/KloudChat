"""Auth wire shapes: camelCase on the wire, snake_case in Python; mirrored by web types."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import AuditEvent, User, UserRole, UserStatus
from app.services import geoip


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


class Wire(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


# single password rule for every path; exposed via `/auth/config`
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 200


class SignupRequest(Wire):
    email: EmailStr
    # upper bound: argon2 DoS guard
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    name: str = Field(min_length=1, max_length=80)


class LoginRequest(Wire):
    email: EmailStr
    password: str = Field(max_length=200)


def _model_picks(value: dict[str, str], keys: set[str]) -> dict[str, str]:
    """Known surfaces only; empty id drops the pick."""
    unknown = set(value) - keys
    if unknown:
        raise ValueError(f"unknown surface: {', '.join(sorted(unknown))}")
    picks = {k: str(v or "").strip()[:200] for k, v in value.items()}
    return {k: v for k, v in picks.items() if v}


class Preferences(Wire):
    """Settings-screen switches, all defaulted."""

    #: off: answer delivered whole at turn end
    stream_responses: bool = True
    auto_memory: bool = False
    show_usage: bool = True
    #: hold space to dictate; fills box, doesn't send
    space_to_talk: bool = False
    #: protected-data default; admin policy caps raw delivery
    privacy_default_action: Literal[
        "ask", "route_strict_local", "mask_external", "send_raw_external"
    ] = "ask"
    about_me: str = Field(default="", max_length=1500)
    response_style: str = Field(default="", max_length=1500)
    #: per-surface/per-a/v-mode picks, synced across browsers; unserved ids fall back on screen
    model_by_kind: dict[str, str] = Field(default_factory=dict)
    av_model_by_mode: dict[str, str] = Field(default_factory=dict)

    @field_validator("model_by_kind")
    @classmethod
    def _surfaces_only(cls, value: dict[str, str]) -> dict[str, str]:
        return _model_picks(value, {"chat", "report", "slides", "image", "av"})

    @field_validator("av_model_by_mode")
    @classmethod
    def _modes_only(cls, value: dict[str, str]) -> dict[str, str]:
        return _model_picks(value, {"audio", "video"})

    @classmethod
    def of(cls, user: User) -> Preferences:
        """Stored over defaults; field or alias keys."""
        stored = user.preferences or {}
        known = set(cls.model_fields) | {f.alias for f in cls.model_fields.values() if f.alias}
        return cls(**{k: v for k, v in stored.items() if k in known})


class ProfilePatch(Wire):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_color: str | None = Field(default=None, max_length=16)
    preferences: Preferences | None = None


class PasswordChange(Wire):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class PasswordForgot(Wire):
    email: EmailStr


class PasswordReset(Wire):
    token: str = Field(max_length=200)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


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
    #: null while verification link outstanding
    email_verified_at: datetime | None = None
    #: last four chars; the key itself never returned
    litellm_key_preview: str | None = None
    litellm_key_issued_at: datetime | None = None
    preferences: Preferences = Preferences()
    #: empty = whole catalogue
    allowed_models: list[str] = []

    @classmethod
    def of(cls, user: User) -> UserOut:
        out = cls.model_validate(user, from_attributes=True)
        out.preferences = Preferences.of(user)
        out.allowed_models = list(user.allowed_models or [])
        return out


class SessionOut(Wire):
    """Login/refresh result; refresh token in httpOnly cookie, `expiresIn` drives silent refresh."""

    access_token: str
    expires_in: int
    user: UserOut


class SignupResponse(Wire):
    """`session` null when approval pending."""

    user: UserOut
    session: SessionOut | None = None


class EmailVerifyResponse(Wire):
    """`active` + session when verification was last step; `pending` awaiting approval."""

    status: UserStatus
    session: SessionOut | None = None


class AccessEventOut(Wire):
    """Own access-log line; client localises `action`, `region` empty when unknown."""

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
    """Live sign-in per refresh-token family; `current` = caller's; fields never guessed."""

    family_id: str
    started_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str
    region: str
    user_agent: str
    current: bool


class AccessEventPage(Wire):
    """Access-log page, newest first, with total."""

    items: list[AccessEventOut]
    total: int
    page: int
    size: int


class ActiveSessionPage(Wire):
    """Live sign-ins page, caller's first; `others` excludes caller."""

    items: list[ActiveSessionOut]
    total: int
    others: int
    page: int
    size: int


class SessionRevokeResult(Wire):
    """Sign-ins ended."""

    revoked: int
