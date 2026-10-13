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
from app.schemas.workspace import ArtifactOut, FileOut


class MessageOut(Wire):
    id: str
    role: Role
    content: str
    steps: list | None = None
    attachments: list | None = None
    usage: dict | None = None
    variants: list | None = None
    superseded: list | None = None
    model: str | None = None
    routing: dict | None = None
    artifact_ids: list | None = None
    #: version of `artifact_ids[0]` written, from the pipeline run; null if unknown
    artifact_version: int | None = None
    #: retired 시작점; read only
    started_from: dict | None = None
    rating: MessageRating | None = None
    failure: TurnFailure | None = None
    created_at: datetime

    @classmethod
    def of(cls, m: Message) -> MessageOut:
        return cls.model_validate(m, from_attributes=True)


class MessageRatingIn(Wire):
    """Explicit null withdraws."""

    rating: MessageRating | None = None


class ImageRequest(Wire):
    """Image generation; `aspect`/`style` folded into prompt."""

    prompt: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    aspect: str = "1:1"
    style: str = ""
    #: retired 서식 id ignored; others refused
    template_id: str | None = Field(default=None, max_length=60)
    #: one upstream call and charge each
    count: int = Field(default=1, ge=1, le=4)
    #: in-document request; adds `imagegen._FIGURE_CLAUSE`
    figure: bool = False
    labels: str = Field(default="auto", max_length=8)
    raw: bool = False


class FigureSuggestRequest(Wire):
    """Figure suggestion for one slide/section."""

    title: str = Field(default="", max_length=300)
    about: str = Field(default="", max_length=300)
    context: str = Field(default="", max_length=4000)
    visual_style: str = Field(default="", max_length=20)


class FigureSuggestion(Wire):
    caption: str
    prompt: str
    #: mermaid kind (`flow`, `method`, `concept`) for diagrams
    figure: str = ""
    #: Korean diagram description; empty for pictures
    description: str = ""
    style: str = ""


class DiagramRequest(Wire):
    """Diagram generation."""

    description: str = Field(min_length=1, max_length=6000)
    figure: str = Field(default="method", max_length=20)
    model: str | None = None
    language: str = Field(default="ko", max_length=5)


class DiagramOut(Wire):
    source: str
    caption: str = ""
    model: str = ""
    credits: int = 0
    #: drawn with .pptx shapes, kept as image artifact
    artifact: ArtifactOut | None = None


class AudioRequest(Wire):
    """Audio generation; `audio_kind` picks model family."""

    prompt: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    audio_kind: Literal["narration", "music"] = "narration"
    voice: str = "alloy"
    #: folded into prompt (no model takes it as a param)
    seconds: int = Field(default=0, ge=0, le=300)


class VideoJobRequest(Wire):
    """Video generation; priced per model×resolution×audio×seconds, unlisted refused."""

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
    prompt: str
    model: str
    params: dict | None

    @classmethod
    def of(cls, job: object) -> JobOut:
        return cls.model_validate(job, from_attributes=True)


class SessionBulkDelete(Wire):
    """Ids, or all owned sessions (resolved server-side)."""

    ids: list[str] = []
    all: bool = False
    #: ignored: artifacts always deleted with the conversation
    artifacts: bool = True


class SessionMade(Wire):
    """Media summary for text-less list rows; measurements only, client phrases it."""

    kind: Literal["image", "video", "narration", "music"]
    count: int
    seconds: int = 0
    aspect: str = ""


#: summarised as-is; `audio` split by `audioKind`
_MEDIA_KINDS = ("image", "video")


def _agreed(rows: list[dict], *keys: str):
    """Truthy value shared by all rows for the first matching key, else None."""
    for key in keys:
        seen = {row.get(key) for row in rows}
        if len(seen) == 1:
            only = seen.pop()
            if only:
                return only
    return None


def made_from_artifacts(rows: list[tuple[str, dict | None]]) -> SessionMade | None:
    """Summary of `(kind, data)` rows, newest first; newest kind only."""
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
    # `actualAspect` measured; `aspect` requested
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
    pipeline_id: str | None = None
    model: str
    routing_mode: RoutingMode
    artifact_id: str | None
    render_template_id: str | None = None
    pending: dict | None = None
    skill_ids: list | None = None
    pinned: bool
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut] | None = None
    preview: str | None = None
    message_count: int = 0
    #: only when there's no text transcript
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
        links: dict[str, tuple[str, int | None]] | None = None,
    ) -> SessionOut:
        from app.services.message_links import linked

        out = cls.model_validate(s, from_attributes=True)
        out.messages = [linked(m, links) for m in messages] if messages is not None else None
        if messages:
            out.preview = snippet(messages[-1].content)
            out.message_count = len(messages)
        else:
            out.preview = preview
            out.message_count = message_count
        out.made = made
        return out


class SessionPage(Wire):
    """대화 기록 page, newest first, with total."""

    items: list[SessionOut]
    total: int
    #: served page; clamped to last
    page: int
    size: int


def snippet(content: str, limit: int = 120) -> str | None:
    """Whitespace-collapsed, truncated to `limit`."""
    text = " ".join((content or "").split())
    if not text:
        return None
    return text[:limit]


class SessionCreate(Wire):
    #: id the client gave its unsent conversation; created on the first send under it
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    kind: SessionKind = SessionKind.chat
    project_id: str | None = None
    agent_id: str | None = None
    model: str | None = None
    routing_mode: RoutingMode = RoutingMode.manual


class MessageForkOut(Wire):
    session: SessionOut
    attachments: list[FileOut]
    attachment_id_map: dict[str, str]


class HandoffIn(Wire):
    #: target document surface; agent must offer it
    kind: Literal["report", "slides"]


class HandoffOut(Wire):
    session: SessionOut
    #: conversation as text file plus its attachments, sent as writer material
    attachments: list[FileOut]


class SessionPatch(Wire):
    title: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None
    model: str | None = None
    routing_mode: RoutingMode | None = None
    project_id: str | None = None
    render_template_id: str | None = Field(default=None, max_length=60)
    #: `[]` clears
    skill_ids: list[str] | None = Field(default=None, max_length=3)

    @field_validator("routing_mode", mode="before")
    @classmethod
    def routing_mode_cannot_be_null(cls, value):
        # omitted = unchanged; column non-null
        if value is None:
            raise ValueError("routing_mode_must_not_be_null")
        return value


class SendMessage(Wire):
    content: str = Field(min_length=1, max_length=200_000)
    attachments: list[str] | None = None
    model: str | None = None
    #: True every turn, False never (unless research asked), "auto" when warranted; None: the
    #: agent's `web_search_default`, else False
    web_search: bool | Literal["auto"] | None = None
    #: per-turn selection; installation alone injects nothing
    activated_skill_ids: list[str] = Field(default_factory=list, max_length=3)
    #: `/design-templates` id, stored on session; `""` clears
    render_template_id: str | None = Field(default=None, max_length=60)
    #: write the pending outline; ignored if none pending
    approve: bool = False
    #: edited outline, sanitised by `_edited_plan`
    plan: dict[str, Any] | None = None
    #: failed latest question to rerun; content/attachments reused
    retry_of: str | None = Field(default=None, max_length=64)
    #: figure card: True with pictures, False without, None unanswered
    include_figures: bool | None = None
    #: stopped-turn answers by question id, added as conditions
    answers: dict[str, str] | None = None
    privacy_action: Literal["route_strict_local", "mask_external", "send_raw_external"] | None = (
        None
    )
    privacy_decision_token: str | None = Field(default=None, max_length=4000)


class Transcription(Wire):
    """Transcription result."""

    text: str
    seconds: int = 0
