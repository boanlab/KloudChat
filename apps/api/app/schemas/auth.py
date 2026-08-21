"""Wire shapes. camelCase out, snake_case in.

The React app reads these straight into `src/types.ts`, so the alias generator
is what keeps the two in sync.
"""

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
    # Long enough to matter, short enough that argon2 does not become a DoS vector.
    password: str = Field(min_length=10, max_length=200)
    name: str = Field(min_length=1, max_length=80)


class LoginRequest(Wire):
    email: EmailStr
    password: str = Field(max_length=200)


class Preferences(Wire):
    """What the settings screen can turn on and off.

    Every field has a default, so an account with nothing stored behaves the way
    the switch says.
    """

    #: Off means the answer appears in one piece when the turn ends.
    stream_responses: bool = True
    #: Extract durable facts from finished turns into memory.
    auto_memory: bool = False
    #: The model · token · credit line under each answer.
    show_usage: bool = True
    #: Default action when protected data would leave for an external model.
    #: The administrator remains the upper bound for raw delivery.
    privacy_default_action: Literal[
        "ask", "route_strict_local", "mask_external", "send_raw_external"
    ] = "ask"

    @classmethod
    def of(cls, user: User) -> Preferences:
        """Stored values over defaults, tolerant of both spellings.

        The column is written with field names and read by code that thinks in
        wire names; going through the schema is what keeps the two in step.
        """
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
    #: Whether the account has its own LiteLLM key, and its last four
    #: characters. The key itself has no route that returns it.
    litellm_key_preview: str | None = None
    litellm_key_issued_at: datetime | None = None
    preferences: Preferences = Preferences()
    #: Empty means the whole catalogue — the default for every account.
    allowed_models: list[str] = []

    @classmethod
    def of(cls, user: User) -> UserOut:
        out = cls.model_validate(user, from_attributes=True)
        # Stored keys win; unknown keys are ignored, so removing a switch does
        # not break an old row.
        out.preferences = Preferences.of(user)
        out.allowed_models = list(user.allowed_models or [])
        return out


class SessionOut(Wire):
    """What a successful login or refresh returns.

    The refresh token is not here: it goes out as an httpOnly cookie, unreadable
    to script. `expiresIn` is what the SPA schedules a silent refresh against.
    """

    access_token: str
    expires_in: int
    user: UserOut


class SignupResponse(Wire):
    """No session when approval is required; the SPA routes to the pending
    screen on `status == "pending"`.
    """

    user: UserOut
    session: SessionOut | None = None


class AccessEventOut(Wire):
    """One line of somebody's own 접속기록.

    `action` travels as the stored verb rather than as a sentence: the screen
    that renders it is the one that knows the reader's language, and a Korean
    string baked in here would come back in English nowhere.

    `region` is empty unless the server has a GeoLite2 database, and empty for
    an address it does not cover. Never a guess — on this screen a wrong city
    is worse than no city, because the reason to look is to spot the one entry
    that was not you.
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
