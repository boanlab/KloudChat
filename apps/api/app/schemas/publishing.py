"""Wire shapes for published agents (공개 챗봇). camelCase out, snake_case in."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.chat import MessageRating
from app.schemas.auth import Wire


class Branding(Wire):
    #: First words on the bot's page, above the box.
    greeting: str = Field(default="", max_length=400)
    #: Up to four questions offered as buttons, sent as written.
    suggested_prompts: list[str] = Field(default_factory=list, max_length=4)
    placeholder: str = Field(default="", max_length=120)


class PublicationSettings(Wire):
    """What an owner sets; every field optional on a change."""

    title: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=400)
    branding: Branding | None = None
    #: Lowered limits (`publishing.DEFAULT_LIMITS` keys); a value above the default is capped.
    limits: dict[str, int] | None = None
    show_process: Literal["none", "summary", "full"] | None = None
    show_sources: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=365)
    conversation_view: Literal["off", "on"] | None = None


Visibility = Literal["internal", "link", "external"]


class PublishIn(PublicationSettings):
    agent_id: str = Field(min_length=1, max_length=64)
    #: Members get the bot at once; `link` and `external` are asked of an administrator.
    visibility: Visibility = "internal"
    note: str = Field(default="", max_length=300)


class ScopeIn(Wire):
    """The owner widens (asked of an administrator) or narrows (at once) the audience."""

    visibility: Visibility
    #: The daily credit cap, required before the bot opens outside.
    credits_per_day: int | None = Field(default=None, ge=1, le=100_000_000)


class ReviewIn(Wire):
    decision: Literal["approve", "reject"]
    #: Required to refuse; the owner reads it.
    reason: str = Field(default="", max_length=300)


class WithdrawIn(Wire):
    """An administrator closes the outside audience: back to members only."""

    reason: str = Field(min_length=4, max_length=300)


class PreflightIn(Wire):
    agent_id: str = Field(min_length=1, max_length=64)


class PublicationPatch(PublicationSettings):
    #: The owner pauses or reopens the bot.
    status: Literal["approved", "paused"] | None = None


class VersionIn(Wire):
    note: str = Field(default="", max_length=300)


class ConsentIn(Wire):
    version: int = Field(ge=1)


class VisitorMessage(Wire):
    content: str = Field(min_length=1, max_length=200_000)
    retry_of: str | None = Field(default=None, max_length=64)


class VisitorRating(Wire):
    rating: MessageRating | None = None


class AdminStatusIn(Wire):
    status: Literal["approved", "paused", "revoked"]
    reason: str = Field(min_length=4, max_length=300)


__all__ = [
    "AdminStatusIn",
    "Branding",
    "ConsentIn",
    "PreflightIn",
    "PublicationPatch",
    "PublicationSettings",
    "PublishIn",
    "ReviewIn",
    "ScopeIn",
    "VersionIn",
    "Visibility",
    "VisitorMessage",
    "VisitorRating",
    "WithdrawIn",
]
