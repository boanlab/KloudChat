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
    #: What this turn made, as artifact ids, for the turns whose answer is a
    #: thing rather than a sentence. The browser renders them where the answer
    #: would be, which is why they travel with the transcript rather than being
    #: looked up from the session — the session points at the newest result
    #: only, and a conversation that made four pictures in two batches has two
    #: answers to show, each under the prompt that asked for it.
    artifact_ids: list | None = None
    #: The 시작점 this turn was begun from, as `{templateId, title}`. Names it
    #: rather than quoting it: the transcript is where what somebody said is
    #: kept, and the template's own sentence was never said by anybody.
    started_from: dict | None = None
    #: What the reader thought of this answer, or null if nobody has said. Sent
    #: with the transcript so a rating outlives the tab it was left in.
    rating: MessageRating | None = None
    #: How this turn ended when it did not end in an answer. On the wire for
    #: the same reason the rating is: the browser already says so while it is
    #: happening, and that notice lives in one tab and is gone on reload.
    failure: TurnFailure | None = None
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
    #: Asked for from inside a document — a slide's picker or a report's — rather
    #: than from the image surface. It changes the prompt, not the model: a
    #: picture going *into* a slide must not be a picture *of* a slide. See
    #: `imagegen._FIGURE_CLAUSE`.
    figure: bool = False


class FigureSuggestRequest(Wire):
    """What the picker asks for when it opens on one 장 or one 절."""

    #: The document's own title, so the suggestion belongs to this document.
    title: str = Field(default="", max_length=300)
    #: The name of the place the picture is going into.
    about: str = Field(default="", max_length=300)
    #: What that place already says, so the picture does not repeat it.
    context: str = Field(default="", max_length=4000)
    #: The document's look — `editorial`, `poster`, `minimal` — so the picture
    #: comes out in the same register. Empty when the document has none.
    visual_style: str = Field(default="", max_length=20)


class FigureSuggestion(Wire):
    caption: str
    prompt: str
    #: The image 서식 the suggestion chose for this place — `image-scene`,
    #: `image-architecture`… Empty when none fits; the picker then draws a
    #: plain picture from `prompt` as before.
    template_id: str = ""
    #: Set when the chosen 서식 is a figure drawn as mermaid (`flow`, `method`,
    #: `concept`) rather than a picture: the client takes the diagram path.
    figure: str = ""
    #: What to draw, in Korean, for the diagram path. Empty for pictures.
    description: str = ""
    #: The style chip to draw with — the 서식's default, or 미니멀 for a
    #: minimal document.
    style: str = ""


class DiagramRequest(Wire):
    """A figure asked for in words — the method, not the picture."""

    description: str = Field(min_length=1, max_length=6000)
    #: `method` · `flow` · `concept`. What kind of figure the words describe.
    figure: str = Field(default="method", max_length=20)
    model: str | None = None
    language: str = Field(default="ko", max_length=5)
    #: The Critic's half: the source the client could not draw, and why.
    broken: str = Field(default="", max_length=20000)
    error: str = Field(default="", max_length=500)


class DiagramOut(Wire):
    source: str
    caption: str = ""
    model: str = ""
    credits: int = 0


class DiagramStore(Wire):
    """A rendered figure, sent back to be kept beside its source."""

    source: str = Field(min_length=1, max_length=20000)
    caption: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=6000)
    figure: str = Field(default="method", max_length=20)
    title: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=120)
    #: PNG bytes, base64. Drawn by the client in the document's face.
    png: str = Field(min_length=1)
    width: int = Field(default=0, ge=0, le=10000)
    height: int = Field(default=0, ge=0, le=10000)


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
    #: Delete what the conversations produced as well. Off by default: the
    #: gallery presents an artifact as a thing in its own right, and a document
    #: somebody put in a project or shared by link should not disappear because
    #: the conversation that started it was tidied away.
    artifacts: bool = False


class SessionMade(Wire):
    """What a session produced, for the line under its title in a list.

    A picture or clip session answers with the thing itself, so its newest
    message is a wordless row and `preview` — the last thing said — has nothing
    to offer. Its name is already the person's prompt, so echoing that
    underneath would say one thing twice. What the list is actually missing is
    the other half: what came back. That is what tells seven clips of one
    request apart.

    Counted rather than described, and sent as measurements rather than as a
    finished sentence, because the sentence has to be written in the reader's
    language and this is the API.
    """

    #: The noun the row prints: `image`, `video`, `narration` or `music`.
    #: Narration and music are separated here although both are `audio`
    #: artifacts, because "내레이션 3개" and "음악 3곡" are what somebody would say.
    kind: Literal["image", "video", "narration", "music"]
    count: int
    #: Zero when unknown and — deliberately — when the artifacts disagree. An
    #: MP3's length is never measured, and four pictures made in two batches at
    #: two ratios have no single ratio; printing one of them would be a claim
    #: about the others.
    seconds: int = 0
    aspect: str = ""


#: Artifact kinds a row names after themselves. Audio is deliberately not one
#: of them: it splits into narration and music on its own `audioKind`. Anything
#: absent here — a report, a deck, a chart — is not summarised this way at all.
_MEDIA_KINDS = ("image", "video")


def _agreed(rows: list[dict], *keys: str):
    """The one value every row gives for a key, or None where they differ.

    A falsy value is not an answer: a missing ratio is stored as `""` and an
    unmeasured length as `0`, so a set of rows where only some carry the fact
    does not agree on it.
    """
    for key in keys:
        seen = {row.get(key) for row in rows}
        if len(seen) == 1:
            only = seen.pop()
            if only:
                return only
    return None


def made_from_artifacts(rows: list[tuple[str, dict | None]]) -> SessionMade | None:
    """`(kind, data)` for one session, newest first, as one summary.

    Only the newest artifact's own kind is counted. A session that made three
    pictures and then a clip is, to the person looking for it, the clip one,
    and "이미지 3장 · 영상 1개" is a row nobody reads.
    """
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
    # What came back before what was asked for: `actualAspect` is measured off
    # the picture while `aspect` is the phrase the prompt asked in, and the two
    # disagree often enough that the artifact panel already shows both.
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
    #: The rendering template this session writes into, if one was picked.
    render_template_id: str | None = None
    #: A generation waiting to be answered or approved, or null.
    #:
    #: Travels with the session rather than with the message that announced it,
    #: because that is where it lives: a reload has to find it, and the screen
    #: has to know that the next thing typed is a note on this rather than a
    #: fresh document.
    pending: dict | None = None
    pinned: bool
    created_at: datetime
    updated_at: datetime
    # Omitted from list responses — the sidebar needs titles, not transcripts.
    messages: list[MessageOut] | None = None
    #: First line of the latest message. The list needs it because a list
    #: response must not carry transcripts.
    preview: str | None = None
    message_count: int = 0
    #: What this session produced, for the rows `preview` cannot serve. Set
    #: only where there is no transcript, so a conversation that happens to
    #: have made a picture still shows what was last said about it.
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
    #: Write the outline that is waiting on this session rather than planning
    #: another one. What 이대로 생성 sends. Ignored where nothing is pending,
    #: and on the surfaces that do not plan first.
    #:
    #: The point of it is that approval is explicit: without this flag every
    #: request plans and offers, so nothing a person has not looked at can
    #: replace a document they already have.
    approve: bool = False
    #: The outline as the person edited it on the card, when they did.
    #:
    #: Approval used to write whatever the planner had stored, so changing one
    #: section heading meant re-prompting and getting a whole new outline back
    #: — a different document, to fix a word. Sent with the approval rather
    #: than saved first: the edit and the decision to write are one gesture,
    #: and a plan stored and then not approved would outlive the card it was
    #: typed on. Sanitised on arrival; see `_edited_plan`.
    plan: dict[str, Any] | None = None
    #: The failed question to run again, by message id — what 다시 시도 sends.
    #: The row is reused rather than written twice: the transcript keeps one
    #: copy of the question, whatever failed under it is replaced, and the model
    #: is not shown the same sentence twice in its history. Only the latest
    #: question qualifies; anything after it would be a conversation that moved
    #: on. `content` and `attachments` are taken from the stored row.
    retry_of: str | None = Field(default=None, max_length=64)
    #: Answer to the figure card, which is the second of the two questions a
    #: document asks before it is written.
    #:
    #: Three states and they are all different. `True` writes the document with
    #: the pictures that were proposed; `False` writes it without them, and the
    #: prose is told so — a section that mentions 아래 그림 with no figure under
    #: it is the failure this whole two-step exists to avoid. `None` is a
    #: message that is not answering the card at all.
    include_figures: bool | None = None
    #: Answers to the questions a stopped turn asked, keyed by question id.
    #: Folded into the request as conditions on it — never substituted for it,
    #: because the sentence they typed is the thing they asked for.
    answers: dict[str, str] | None = None
    privacy_action: Literal[
        "route_strict_local", "mask_external", "send_raw_external"
    ] | None = None
    privacy_decision_token: str | None = Field(default=None, max_length=4000)
