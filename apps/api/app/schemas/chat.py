from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models.chat import ChatSession, Message, MessageRating, Role, RoutingMode, SessionKind
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
    #: The 시작점 this turn was begun from, as `{templateId, title}`. Names it
    #: rather than quoting it: the transcript is where what somebody said is
    #: kept, and the template's own sentence was never said by anybody.
    started_from: dict | None = None
    #: What the reader thought of this answer, or null if nobody has said. Sent
    #: with the transcript so a rating outlives the tab it was left in.
    rating: MessageRating | None = None
    created_at: datetime

    @classmethod
    def of(cls, m: Message) -> MessageOut:
        return cls.model_validate(m, from_attributes=True)


class MessageRatingIn(Wire):
    """One verdict, or its withdrawal.

    Null is a first-class value here: pressing the lit thumb again takes the
    rating off, and that has to be expressible rather than merely absent.
    """

    rating: MessageRating | None = None


class ImageRequest(Wire):
    """What the image surface sends.

    `aspect` and `style` have no API parameter and are folded into the prompt,
    so they record what was asked for rather than what came back.
    """

    prompt: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    aspect: str = "1:1"
    style: str = ""
    #: An `image` design template. It shapes the prompt rather than producing a
    #: file, so unlike the deck and document templates nothing is stored under
    #: its name — the picture is the whole output.
    template_id: str | None = Field(default=None, max_length=60)
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
    #: How long the clip should be. No audio model here takes a duration
    #: parameter, so it is folded into the prompt the way an image's aspect
    #: ratio is — which makes it a request rather than a setting, and the
    #: artifact records both what was asked for and what came back.
    seconds: int = Field(default=0, ge=0, le=300)


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
    #: The rendering template this session writes into, if one was picked.
    render_template_id: str | None = None
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
    #: A 시작점 attached to this one comparison. See `SendMessage`.
    starting_template_id: str | None = Field(default=None, max_length=64)
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
    render_template_id: str | None = Field(default=None, max_length=60)

    @field_validator("routing_mode", mode="before")
    @classmethod
    def routing_mode_cannot_be_null(cls, value):
        # Omitted means "leave unchanged"; explicit null would otherwise be
        # assigned to the non-null database column and surface as a 500.
        if value is None:
            raise ValueError("routing_mode_must_not_be_null")
        return value


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
    #: A 시작점 — a built-in from `/prompt-templates`, or a `templates` row the
    #: caller can see. Carried by the turn the way an activated skill is: it
    #: reaches the model as its own context block, and `content` stays the words
    #: the person typed.
    #:
    #: Not sticky, unlike `render_template_id`: a starting point starts one
    #: turn, and a shape is worn by the whole conversation.
    starting_template_id: str | None = Field(default=None, max_length=64)
    #: A rendering template from `/design-templates`. Sticky: it is stored on
    #: the session, so a follow-up turn keeps the shape without resending it.
    #: `""` clears it, which is how somebody goes back to the built-in track.
    render_template_id: str | None = Field(default=None, max_length=60)
    privacy_action: Literal[
        "route_strict_local", "mask_external", "send_raw_external"
    ] | None = None
    privacy_decision_token: str | None = Field(default=None, max_length=4000)
