from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.chat import ChatSession, Message, Role, RoutingMode, SessionKind
from app.schemas.auth import Wire


class MessageOut(Wire):
    id: str
    role: Role
    content: str
    steps: list | None = None
    attachments: list | None = None
    usage: dict | None = None
    variants: list | None = None
    model: str | None = None
    routing: dict | None = None
    created_at: datetime

    @classmethod
    def of(cls, m: Message) -> MessageOut:
        return cls.model_validate(m, from_attributes=True)


class ImageRequest(Wire):
    """What the image surface sends.

    `aspect` and `style` have no API parameter and are folded into the prompt,
    so they record what was asked for rather than what came back.
    """

    prompt: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    aspect: str = "1:1"
    style: str = ""
    #: Up to four. Each is a separate upstream call and a separate charge.
    count: int = Field(default=1, ge=1, le=4)


class AudioRequest(Wire):
    """What the a/v surface sends for a sound clip.

    `kind` picks the model family: speech and music are different products from
    different providers. There is no sound-effect option — nothing serves it.
    """

    prompt: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    audio_kind: Literal["narration", "music"] = "narration"
    voice: str = "alloy"


class VideoJobRequest(Wire):
    """One clip. Every field is priced: the pass-through charges a fixed figure
    per (model × resolution × audio × duration), and an unlisted combination is
    refused rather than billed at a guess."""

    prompt: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    resolution: Literal["720p", "1080p"] = "720p"
    seconds: int = Field(default=4, ge=4, le=8)
    audio: bool = False
    aspect: str = "16:9"


class JobOut(Wire):
    id: str
    session_id: str
    kind: str
    status: str
    progress: int
    stage: str
    credits_used: int
    credits_estimated: int
    error: str | None
    artifact_id: str | None
    created_at: datetime
    finished_at: datetime | None
    #: What was asked for. On the wire because a failed job's card offers a
    #: retry, which needs the request to rebuild.
    prompt: str
    model: str
    params: dict | None

    @classmethod
    def of(cls, job: object) -> JobOut:
        return cls.model_validate(job, from_attributes=True)


class SessionBulkDelete(Wire):
    """Ids to remove, or every conversation the caller owns.

    `all` is resolved server-side at request time, so a conversation started in
    another tab is not silently spared.
    """

    ids: list[str] = []
    all: bool = False


class SessionOut(Wire):
    id: str
    kind: SessionKind
    title: str
    project_id: str | None
    agent_id: str | None
    model: str
    routing_mode: RoutingMode
    artifact_id: str | None
    pinned: bool
    created_at: datetime
    updated_at: datetime
    # Omitted from list responses — the sidebar needs titles, not transcripts.
    messages: list[MessageOut] | None = None
    #: First line of the latest message. The list needs it because a list
    #: response must not carry transcripts.
    preview: str | None = None
    message_count: int = 0

    @classmethod
    def of(
        cls,
        s: ChatSession,
        messages: list[Message] | None = None,
        *,
        preview: str | None = None,
        message_count: int = 0,
    ) -> SessionOut:
        out = cls.model_validate(s, from_attributes=True)
        out.messages = [MessageOut.of(m) for m in messages] if messages is not None else None
        if messages:
            out.preview = snippet(messages[-1].content)
            out.message_count = len(messages)
        else:
            out.preview = preview
            out.message_count = message_count
        return out


def snippet(content: str, limit: int = 120) -> str | None:
    """One line, short. Newlines in a list row render as a run-on sentence."""
    text = " ".join((content or "").split())
    if not text:
        return None
    return text[:limit]


class SessionCreate(Wire):
    kind: SessionKind = SessionKind.chat
    project_id: str | None = None
    agent_id: str | None = None
    model: str | None = None
    routing_mode: RoutingMode = RoutingMode.manual


class CompareRequest(Wire):
    content: str = Field(min_length=1)
    #: Two or three. One is an ordinary turn; more is a wall of columns and an
    #: unexpected bill.
    models: list[str] = Field(min_length=2, max_length=3)
    #: Installed skills explicitly selected for this one comparison.
    activated_skill_ids: list[str] = Field(default_factory=list, max_length=3)
    attachments: list[str] | None = None
    privacy_action: Literal[
        "route_strict_local", "mask_external", "send_raw_external"
    ] | None = None
    privacy_decision_token: str | None = Field(default=None, max_length=4000)


class ChooseVariant(Wire):
    model: str = Field(min_length=1, max_length=200)


class SessionPatch(Wire):
    title: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None
    model: str | None = None
    routing_mode: RoutingMode | None = None
    project_id: str | None = None


class SendMessage(Wire):
    content: str = Field(min_length=1, max_length=200_000)
    attachments: list[str] | None = None
    #: Model override for this turn only; falls back to the session's.
    model: str | None = None
    #: The composer's toggle, off by default: searching changes the latency and
    #: the character of the answer.
    web_search: bool = False
    #: Installed skills explicitly selected for this one turn. Empty means no
    #: skill; installation alone never injects a procedure.
    activated_skill_ids: list[str] = Field(default_factory=list, max_length=3)
    privacy_action: Literal[
        "route_strict_local", "mask_external", "send_raw_external"
    ] | None = None
    privacy_decision_token: str | None = Field(default=None, max_length=4000)
