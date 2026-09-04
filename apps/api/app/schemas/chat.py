"""Wire schemas for sessions, messages, jobs and media requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.models.chat import (
    ChatSession,
    Message,
    MessageRating,
    Role,
    RoutingMode,
    SessionKind,
    TurnFailure,
)
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
    #: Artifacts this turn produced, rendered in place of the answer.
    artifact_ids: list | None = None
    #: The 시작점 this turn began from, as `{templateId, title}`.
    started_from: dict | None = None
    rating: MessageRating | None = None
    #: How the turn ended when it did not end in an answer.
    failure: TurnFailure | None = None
    created_at: datetime

    @classmethod
    def of(cls, m: Message) -> MessageOut:
        return cls.model_validate(m, from_attributes=True)


class MessageRatingIn(Wire):
    """A rating; explicit null withdraws it."""

    rating: MessageRating | None = None


class ImageRequest(Wire):
    """Image generation request. `aspect` and `style` are folded into the prompt."""

    prompt: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    aspect: str = "1:1"
    style: str = ""
    #: An `image` design template; shapes the prompt only.
    template_id: str | None = Field(default=None, max_length=60)
    #: Each picture is a separate upstream call and charge.
    count: int = Field(default=1, ge=1, le=4)
    #: Requested from inside a document; adds `imagegen._FIGURE_CLAUSE` to the prompt.
    figure: bool = False
    #: In-picture text language: `auto`, `ko`, `en`, `none`.
    labels: str = Field(default="auto", max_length=8)
    #: Send the prompt as typed, skipping the planner.
    raw: bool = False


class FigureSuggestRequest(Wire):
    """Figure suggestion request for one slide or section."""

    #: Document title.
    title: str = Field(default="", max_length=300)
    #: Heading of the slide or section the picture goes into.
    about: str = Field(default="", max_length=300)
    #: Existing text of that slide or section.
    context: str = Field(default="", max_length=4000)
    #: Document look: `editorial`, `poster`, `minimal`, or empty.
    visual_style: str = Field(default="", max_length=20)


class FigureSuggestion(Wire):
    caption: str
    prompt: str
    #: Image 서식 chosen, e.g. `image-scene`; empty draws a plain picture from `prompt`.
    template_id: str = ""
    #: Mermaid figure kind (`flow`, `method`, `concept`) when the client should draw a diagram.
    figure: str = ""
    #: Diagram description, in Korean. Empty for pictures.
    description: str = ""
    #: Style chip to draw with.
    style: str = ""


class DiagramRequest(Wire):
    """Diagram generation request."""

    description: str = Field(min_length=1, max_length=6000)
    #: `method`, `flow` or `concept`.
    figure: str = Field(default="method", max_length=20)
    model: str | None = None
    language: str = Field(default="ko", max_length=5)
    #: Mermaid source the client failed to render, and the renderer's error.
    broken: str = Field(default="", max_length=20000)
    error: str = Field(default="", max_length=500)


class DiagramOut(Wire):
    source: str
    caption: str = ""
    model: str = ""
    credits: int = 0


class DiagramStore(Wire):
    """A client-rendered diagram stored beside its source."""

    source: str = Field(min_length=1, max_length=20000)
    caption: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=6000)
    figure: str = Field(default="method", max_length=20)
    title: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=120)
    #: PNG bytes, base64.
    png: str = Field(min_length=1)
    width: int = Field(default=0, ge=0, le=10000)
    height: int = Field(default=0, ge=0, le=10000)


class AudioRequest(Wire):
    """Audio generation request. `audio_kind` picks the model family."""

    prompt: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    audio_kind: Literal["narration", "music"] = "narration"
    voice: str = "alloy"
    #: Requested length; folded into the prompt, no model takes it as a parameter.
    seconds: int = Field(default=0, ge=0, le=300)


class VideoJobRequest(Wire):
    """Video generation request.

    Priced per (model × resolution × audio × seconds); unlisted combinations are refused.
    """

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
    #: Original request, so a failed job can be retried.
    prompt: str
    model: str
    params: dict | None

    @classmethod
    def of(cls, job: object) -> JobOut:
        return cls.model_validate(job, from_attributes=True)


class SessionBulkDelete(Wire):
    """Ids to delete, or every session the caller owns; `all` is resolved server-side."""

    ids: list[str] = []
    all: bool = False
    #: Also delete the artifacts those sessions produced.
    artifacts: bool = False


class SessionMade(Wire):
    """Summary of the media a session produced, for list rows that have no text preview.

    Measurements only; the client writes the sentence in the reader's language.
    """

    kind: Literal["image", "video", "narration", "music"]
    count: int
    #: Zero when unknown or when the artifacts disagree.
    seconds: int = 0
    #: Empty when unknown or when the artifacts disagree.
    aspect: str = ""


#: Artifact kinds summarised as-is; `audio` splits into narration/music by `audioKind`.
_MEDIA_KINDS = ("image", "video")


def _agreed(rows: list[dict], *keys: str):
    """The one truthy value every row gives for a key, else None."""
    for key in keys:
        seen = {row.get(key) for row in rows}
        if len(seen) == 1:
            only = seen.pop()
            if only:
                return only
    return None


def made_from_artifacts(rows: list[tuple[str, dict | None]]) -> SessionMade | None:
    """Summarises `(kind, data)` rows, newest first; only the newest artifact's kind is counted."""
    if not rows:
        return None
    kind, newest = rows[0][0], rows[0][1] or {}
    same = [data or {} for k, data in rows if k == kind]
    if kind == "audio":
        noun = "music" if newest.get("audioKind") == "music" else "narration"
    elif kind in _MEDIA_KINDS:
        noun = kind
    else:
        return None
    seconds = _agreed(same, "durationSec") if noun != "image" else None
    # `actualAspect` is measured off the picture; `aspect` is what was asked for.
    aspect = _agreed(same, "actualAspect", "aspect") if noun != "narration" else None
    return SessionMade(
        kind=noun,
        count=len(same),
        seconds=int(seconds or 0),
        aspect=str(aspect or ""),
    )


class SessionOut(Wire):
    id: str
    kind: SessionKind
    title: str
    project_id: str | None
    agent_id: str | None
    model: str
    routing_mode: RoutingMode
    artifact_id: str | None
    render_template_id: str | None = None
    #: A generation awaiting an answer or approval, or null.
    pending: dict | None = None
    pinned: bool
    created_at: datetime
    updated_at: datetime
    #: Omitted from list responses.
    messages: list[MessageOut] | None = None
    #: First line of the latest message.
    preview: str | None = None
    message_count: int = 0
    #: Media summary; set only when there is no text transcript.
    made: SessionMade | None = None

    @classmethod
    def of(
        cls,
        s: ChatSession,
        messages: list[Message] | None = None,
        *,
        preview: str | None = None,
        message_count: int = 0,
        made: SessionMade | None = None,
    ) -> SessionOut:
        out = cls.model_validate(s, from_attributes=True)
        out.messages = [MessageOut.of(m) for m in messages] if messages is not None else None
        if messages:
            out.preview = snippet(messages[-1].content)
            out.message_count = len(messages)
        else:
            out.preview = preview
            out.message_count = message_count
        out.made = made
        return out


def snippet(content: str, limit: int = 120) -> str | None:
    """Collapses whitespace and truncates to `limit` characters."""
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
    models: list[str] = Field(min_length=2, max_length=3)
    activated_skill_ids: list[str] = Field(default_factory=list, max_length=3)
    #: See `SendMessage.starting_template_id`.
    starting_template_id: str | None = Field(default=None, max_length=64)
    attachments: list[str] | None = None
    privacy_action: Literal["route_strict_local", "mask_external", "send_raw_external"] | None = (
        None
    )
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
        # Omitted means unchanged; the column is non-null.
        if value is None:
            raise ValueError("routing_mode_must_not_be_null")
        return value


class SendMessage(Wire):
    content: str = Field(min_length=1, max_length=200_000)
    attachments: list[str] | None = None
    #: Model override for this turn only.
    model: str | None = None
    web_search: bool = False
    #: Installed skills selected for this turn; installation alone injects nothing.
    activated_skill_ids: list[str] = Field(default_factory=list, max_length=3)
    #: A 시작점: a built-in from `/prompt-templates` or a visible `templates` row.
    #: Applies to this turn only; reaches the model as its own context block.
    starting_template_id: str | None = Field(default=None, max_length=64)
    #: A rendering template from `/design-templates`. Stored on the session;
    #: `""` clears it.
    render_template_id: str | None = Field(default=None, max_length=60)
    #: Write the pending outline instead of planning again. Ignored when
    #: nothing is pending.
    approve: bool = False
    #: The pending outline as edited on the card. Sanitised by `_edited_plan`.
    plan: dict[str, Any] | None = None
    #: Message id of the failed latest question to run again; its row is
    #: reused and `content`/`attachments` come from it.
    retry_of: str | None = Field(default=None, max_length=64)
    #: Figure-card answer: `True` writes with the proposed pictures, `False`
    #: without, `None` is not answering the card.
    include_figures: bool | None = None
    #: Answers to a stopped turn's questions, keyed by question id; added as
    #: conditions on the request.
    answers: dict[str, str] | None = None
    privacy_action: Literal["route_strict_local", "mask_external", "send_raw_external"] | None = (
        None
    )
    privacy_decision_token: str | None = Field(default=None, max_length=4000)


class Transcription(Wire):
    """Transcription result."""

    text: str
    #: Audio length reported by the backend, or 0.
    seconds: int = 0
