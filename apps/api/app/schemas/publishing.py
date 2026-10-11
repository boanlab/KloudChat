"""Published-agent (공개 챗봇) wire shapes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.chat import MessageRating
from app.schemas.auth import Wire


class Branding(Wire):
    greeting: str = Field(default="", max_length=400)
    #: ≤4 button prompts, sent verbatim
    suggested_prompts: list[str] = Field(default_factory=list, max_length=4)
    placeholder: str = Field(default="", max_length=120)


class PublicationSettings(Wire):
    """Owner settings; all optional on change."""

    title: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=400)
    branding: Branding | None = None
    #: lowered `DEFAULT_LIMITS`; above-default values capped
    limits: dict[str, int] | None = None
    show_process: Literal["none", "summary", "full"] | None = None
    show_sources: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=365)
    conversation_view: Literal["off", "on"] | None = None


Visibility = Literal["internal", "link", "external"]


class PublishIn(PublicationSettings):
    agent_id: str = Field(min_length=1, max_length=64)
    #: `link`/`external` need admin approval
    visibility: Visibility = "internal"
    note: str = Field(default="", max_length=300)


class ScopeIn(Wire):
    """Audience change: widening reviewed, narrowing immediate."""

    visibility: Visibility
    #: daily credit cap; required for outside audiences
    credits_per_day: int | None = Field(default=None, ge=1, le=100_000_000)


class ReviewIn(Wire):
    decision: Literal["approve", "reject"]
    #: required for reject; shown to owner
    reason: str = Field(default="", max_length=300)


class WithdrawIn(Wire):
    """Admin closes outside audience (members only)."""

    reason: str = Field(min_length=4, max_length=300)


class PreflightIn(Wire):
    agent_id: str = Field(min_length=1, max_length=64)


class PublicationPatch(PublicationSettings):
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


class EmbedChannel(Wire):
    on: bool = False
    #: `https://host[:port]` or `https://*.host`, ≤10
    origins: list[str] = Field(default_factory=list, max_length=10)


class ChannelsIn(Wire):
    """Full desired channel set; omitted channels close."""

    embed: EmbedChannel | None = None
    api: bool = False
    mcp: bool = False


class KeyIn(Wire):
    name: str = Field(min_length=1, max_length=80)


class AdminStatusIn(Wire):
    status: Literal["approved", "paused", "revoked"]
    reason: str = Field(min_length=4, max_length=300)


__all__ = [
    "AdminStatusIn",
    "Branding",
    "ChannelsIn",
    "ConsentIn",
    "EmbedChannel",
    "KeyIn",
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
