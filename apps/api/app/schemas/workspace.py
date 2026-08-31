"""Wire shapes for the workspace resources. camelCase out, snake_case in."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from app.models.user import ApiKey
from app.models.workspace import (
    Agent,
    AgentVisibility,
    Artifact,
    ArtifactKind,
    Connector,
    ConnectorStatus,
    ConnectorTool,
    DesignSystem,
    Memory,
    MemoryType,
    Project,
    Share,
    ShareScope,
    ShareView,
    Skill,
    SkillSource,
    StoredFile,
    Template,
    Transport,
    Visibility,
)
from app.schemas.auth import Wire
from app.services import design as design_service
from app.services import geoip
from app.services.prompt_templates import PromptTemplate

#: JSONB list columns are nullable in the database, but the wire contract is a
#: list either way — absorbed here rather than in every `of()` and consumer.
JsonList = Annotated[list[str], BeforeValidator(lambda v: v or [])]
#: The same absorption for a JSONB map column.
JsonMap = Annotated[dict[str, str], BeforeValidator(lambda v: v or {})]


# ── files ──────────────────────────────────────────────────────────────
class OpenedDocument(Wire):
    """Where the document a file was read into now lives."""

    id: str


class FileOut(Wire):
    id: str
    name: str
    size: int
    mime: str
    tokens: int
    project_id: str | None
    session_id: str | None
    #: Set when the file is an agent's searchable knowledge.
    agent_id: str | None = None
    #: Set when the text was ingested from a page rather than uploaded.
    source_url: str | None = None
    #: First few hundred characters, so the UI can show what was actually read.
    preview: str = ""
    error: str | None = None
    #: False when the vector index does not cover this document. The panel says
    #: so rather than leaving it to look the same as one that is covered.
    indexed: bool = False
    created_at: datetime

    @classmethod
    def of(cls, f: StoredFile) -> FileOut:
        out = cls.model_validate(f, from_attributes=True)
        out.preview = f.text[:280]
        out.indexed = f.indexed_at is not None
        return out


# ── projects ───────────────────────────────────────────────────────────
class ProjectOut(Wire):
    id: str
    name: str
    description: str
    emoji: str
    instructions: str
    skill_ids: JsonList = Field(default_factory=list)
    design_system_id: str | None = None
    #: Surface → rendering template. What a new session here starts in.
    render_templates: JsonMap = Field(default_factory=dict)
    files: list[FileOut] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(
        cls,
        p: Project,
        files: list[StoredFile] | None = None,
        session_ids: list[str] | None = None,
    ) -> ProjectOut:
        out = cls.model_validate(p, from_attributes=True)
        out.skill_ids = list(p.skill_ids or [])
        out.render_templates = dict(p.render_templates or {})
        out.files = [FileOut.of(f) for f in (files or [])]
        out.session_ids = list(session_ids or [])
        return out


class ProjectIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    emoji: str = "📁"
    instructions: str = ""
    skill_ids: list[str] | None = None
    design_system_id: str | None = None
    render_templates: dict[str, str] | None = None


class ProjectPatch(Wire):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = None
    emoji: str | None = None
    instructions: str | None = None
    skill_ids: list[str] | None = None
    #: Explicit null clears it, so `exclude_unset` is what separates "no design
    #: system" from "leave the design system alone".
    design_system_id: str | None = None
    #: Sent whole, not per surface: a map with one key is how a picker clears
    #: the other surface, and a merge here would make that impossible to say.
    render_templates: dict[str, str] | None = None


# ── artifacts ──────────────────────────────────────────────────────────

#: How much of a written body a card carries. Enough to recognise the document
#: in a grid; nowhere near enough to render or edit it, which is what the flag
#: below is for.
_CARD_CHARS = 400

#: Sections and slides a card carries. A thumbnail shows the top of a document,
#: so the rest of it is weight the list pays for on every open.
_CARD_PARTS = 4


def _card_data(kind: ArtifactKind, data: dict[str, Any] | None) -> dict[str, Any] | None:
    """The body a listing needs, which is much less than the body.

    Measured before this existed: 385 artifacts came to 4.0 MB, and 2.8 MB of
    it was the `content` and `blocks` of 69 HTML documents that the grid draws
    as thumbnails the size of a business card. Media artifacts are already
    small — a `src` and a duration — so they travel whole.

    **Every key survives; only the values shrink.** The client's artifact types
    declare these fields, and a renderer that reads `sources.length` on a card
    with no `sources` takes the screen down with it — which is exactly what the
    first version of this function did.
    """
    if not data:
        return data
    if kind in (ArtifactKind.html, ArtifactKind.code):
        # The markup is the whole artifact and none of it fits on a card. The
        # client fetches the document when it is about to show or edit one.
        return {**data, "content": "", "blocks": []}
    if kind is ArtifactKind.deck:
        return {
            **data,
            "slides": [
                {k: v for k, v in (slide or {}).items() if k in ("id", "title", "layout")}
                for slide in (data.get("slides") or [])[:_CARD_PARTS]
            ],
        }
    if kind is ArtifactKind.report:
        return {
            **data,
            # Citations are a third of a report's weight and none of its card.
            "sources": [],
            "sections": [
                {
                    **{k: v for k, v in (section or {}).items() if k != "content"},
                    "content": str((section or {}).get("content") or "")[:_CARD_CHARS],
                    # The pictures a browser drew of this section's mermaid
                    # diagrams, as base64 PNGs. They arrived on the section when
                    # diagrams learned to reach the exported file, and nothing
                    # trimmed them here: measured on one real account, 384 KB of
                    # a 442 KB listing was diagrams — 86% of a payload whose
                    # whole purpose is to be small. A card draws a thumbnail and
                    # has never shown one.
                    #
                    # Emptied rather than dropped, like every other value here:
                    # a renderer reading `Object.keys(section.diagrams)` on a
                    # card that has no `diagrams` takes the screen down.
                    "diagrams": {},
                }
                for section in (data.get("sections") or [])[:_CARD_PARTS]
            ],
        }
    return data


class ArtifactOut(Wire):
    id: str
    kind: ArtifactKind
    title: str
    version: int
    data: dict[str, Any] | None
    session_id: str | None
    project_id: str | None
    created_at: datetime
    updated_at: datetime
    #: True when `data` was cut down for a listing. Anything that renders the
    #: whole document — or lets somebody edit it — fetches it by id first, or
    #: it would save a truncated copy over the real one.
    partial: bool = False

    @classmethod
    def of(cls, a: Artifact) -> ArtifactOut:
        return cls.model_validate(a, from_attributes=True)

    @classmethod
    def card(cls, a: Artifact) -> ArtifactOut:
        """One row of a listing: the same shape, with the body cut down."""
        out = cls.model_validate(a, from_attributes=True)
        trimmed = _card_data(a.kind, a.data)
        out.partial = trimmed is not a.data
        out.data = trimmed
        return out


class ArtifactIn(Wire):
    kind: ArtifactKind
    title: str = ""
    data: dict[str, Any] | None = None
    session_id: str | None = None
    project_id: str | None = None


class ArtifactVersionOut(Wire):
    version: int
    summary: str
    created_at: datetime

    @classmethod
    def of(cls, v: object) -> ArtifactVersionOut:
        return cls.model_validate(v, from_attributes=True)


class ArtifactRestore(Wire):
    version: int


class ArtifactPatch(Wire):
    title: str | None = None
    data: dict[str, Any] | None = None
    project_id: str | None = None
    #: One line describing the edit, kept with the superseded version.
    summary: str = ""
    #: The version the editor was working from.
    #:
    #: A PATCH carries the whole document, so two people editing the same
    #: report means the second save replaces the first person's paragraphs
    #: with their own copy of the older text. Sent, the server refuses rather
    #: than overwriting. Omitted, the write goes through as before — the
    #: browser is not the only client, and a caller that has not been taught
    #: to send it should not start failing.
    expected_version: int | None = None


class SlideFactCheck(Wire):
    """Which slide to check."""

    slide_id: str


class DiagramPicture(Wire):
    """A diagram the browser drew, on its way to being stored.

    Mermaid draws in a browser and nothing on the server can, so the picture
    has to come back from the reader who happened to open the document. Keyed
    by the diagram's own source — see `report_export.diagram_key` — because a
    section whose diagrams are numbered loses them all when somebody adds one
    in the middle.
    """

    section_id: str
    key: str = Field(min_length=8, max_length=64)
    #: A `data:` picture. Anything else is refused rather than fetched.
    src: str = Field(max_length=4_000_000)


class SectionFactCheck(Wire):
    """Which report section to check.

    One section, not the report. A whole-document run is a hundred searches
    nobody asked for, and a hundred verdicts is not something a reader can act
    on — the deck screen settled this question the same way.
    """

    section_id: str


class SlideImage(Wire):
    """A picture this workspace already made, put on one slide of a JSON deck.

    By slide id rather than by position: a deck's slides carry ids and a
    person may reorder them between choosing and sending.
    """

    slide_id: str = Field(min_length=1, max_length=64)
    artifact_id: str = Field(min_length=1, max_length=64)
    caption: str = Field(default="", max_length=200)


class BlockImage(Wire):
    """A picture this workspace already made, put into one block of a page.

    The picture travels by id rather than as bytes: it is already stored, the
    caller is already the owner, and an upload path here would be a second way
    to get a file into a document that the file rules would have to learn.
    """

    index: int = Field(ge=0, le=63)
    #: The `image` artifact to embed.
    artifact_id: str = Field(min_length=1, max_length=64)
    #: Printed under the picture. Empty leaves the figure uncaptioned rather
    #: than repeating the prompt, which is a request and not a caption.
    caption: str = Field(default="", max_length=200)


class SectionImage(Wire):
    """A picture this workspace already made, put into one section of a report.

    A report is Markdown, and a Markdown picture — `![caption](data:…)` — is a
    shape `richtext` and all three exporters already read. So this writes a line
    into the body rather than inventing a field: what the `.docx` draws for a
    figure the writer proposed, it draws for this one, by the same code.
    """

    #: The section to append it to. By id rather than by index, so a picture does
    #: not move when somebody adds a section above it.
    section_id: str = Field(min_length=1, max_length=64)
    #: The `image` artifact to embed.
    artifact_id: str = Field(min_length=1, max_length=64)
    #: Printed under the picture. Empty leaves the figure uncaptioned rather
    #: than repeating the prompt, which is a request and not a caption.
    caption: str = Field(default="", max_length=200)


class BlockRewrite(Wire):
    """Which block of an HTML artifact, and why it is being rewritten.

    By position rather than by id: blocks are ordered, a rewrite never changes
    how many there are, and the artifacts written before this existed carry no
    ids to address.
    """

    index: int = Field(ge=0, le=63)
    #: What to change. Empty means "just try again".
    note: str = Field(default="", max_length=600)


class SectionRewrite(Wire):
    """Which section, and why it is being rewritten."""

    section_id: str
    #: What to change. Optional — an empty note means "just try again".
    note: str = ""


class SlideRewrite(Wire):
    """Which slide, and why it is being rewritten.

    A separate schema from `SectionRewrite` even though the fields match: the
    two surfaces name their parts differently, and a `section_id` in a deck
    request would be a field the caller has to translate on the way in.
    """

    slide_id: str
    #: What to change. Optional — an empty note means "just try again".
    note: str = ""


# ── skills ─────────────────────────────────────────────────────────────
class SkillOut(Wire):
    id: str
    name: str
    slug: str
    description: str
    when_to_use: str
    body: str
    catalog_key: str | None = None
    source: SkillSource
    kinds: JsonList = Field(default_factory=list)
    required_tools: JsonList = Field(default_factory=list)
    estimated_tokens: int = 0
    version: str
    enabled: bool
    #: Shared to the store, exactly as an agent is.
    visibility: Visibility = Visibility.private
    installs: int = 0
    #: The shared row this one was copied from, if it was copied.
    origin_id: str | None = None
    updated_at: datetime

    @classmethod
    def of(cls, s: Skill) -> SkillOut:
        out = cls.model_validate(s, from_attributes=True)
        out.kinds = list(s.kinds or [])
        out.required_tools = list(s.required_tools or [])
        return out


class StoreSkillOut(SkillOut):
    """A skill as the store lists it: somebody else's, and not yet yours.

    `installed` is what the card reads. Without it the only way to know a copy
    is already sitting on the skills screen is to take a second one and find
    out, which is the mistake the flag exists to make impossible.
    """

    owner_id: str
    owner_name: str = ""
    #: Published by an administrator, so the store can separate the entries the
    #: workspace ships with from the ones colleagues wrote.
    official: bool = False
    installed: bool = False

    @classmethod
    def store(
        cls,
        s: Skill,
        *,
        owner_name: str = "",
        official: bool = False,
        installed: bool = False,
    ) -> StoreSkillOut:
        out = cls.model_validate(s, from_attributes=True)
        out.kinds = list(s.kinds or [])
        out.required_tools = list(s.required_tools or [])
        out.owner_name = owner_name
        out.official = official
        out.installed = installed
        return out


class SkillIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    when_to_use: str = ""
    body: str = ""
    kinds: list[str] | None = None
    required_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    visibility: Visibility = Visibility.private


# ── memories ───────────────────────────────────────────────────────────
class MemoryOut(Wire):
    id: str
    name: str
    description: str
    type: MemoryType
    body: str
    scope: str
    links: JsonList = Field(default_factory=list)
    pinned: bool
    updated_at: datetime

    @classmethod
    def of(cls, m: Memory) -> MemoryOut:
        out = cls.model_validate(m, from_attributes=True)
        out.links = list(m.links or [])
        return out


class MemoryIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    type: MemoryType = MemoryType.user
    body: str = ""
    scope: str = "global"
    links: list[str] | None = None
    pinned: bool = False


# ── agents ─────────────────────────────────────────────────────────────
class AgentOut(Wire):
    id: str
    name: str
    slug: str
    description: str
    model: str
    system_prompt: str
    tools: list[str] | None = None
    skill_ids: list[str] | None = None
    kinds: JsonList = Field(default_factory=list)
    temperature: float
    color: str
    enabled: bool
    visibility: AgentVisibility
    installs: int
    catalog_key: str | None = None
    origin_id: str | None = None
    #: Published by an administrator. Store-only; false on your own rows.
    official: bool = False
    #: This caller already holds a copy. Store-only, for the same reason as on
    #: a skill: a second copy is not what the button was pressed for.
    installed: bool = False
    runs: int
    #: Runtime-only: the caller has readable text on this agent's shelf, so
    #: `search_knowledge` can actually be built for a chat turn.
    has_knowledge: bool = False
    #: Who made it. The store mixes other people's agents with your own, and
    #: without this the edit button's outcome is unknowable until it 403s.
    owner_id: str
    owner_name: str = ""
    updated_at: datetime

    @classmethod
    def of(
        cls,
        a: Agent,
        owner_name: str = "",
        *,
        has_knowledge: bool = False,
        official: bool = False,
        installed: bool = False,
    ) -> AgentOut:
        out = cls.model_validate(a, from_attributes=True)
        out.owner_name = owner_name
        out.has_knowledge = has_knowledge
        out.official = official
        out.installed = installed
        out.tools = None if a.tools is None else list(a.tools)
        out.skill_ids = None if a.skill_ids is None else list(a.skill_ids)
        out.kinds = list(a.kinds or [])
        return out


class AgentIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    #: The @handle. Slugified server-side, unique per owner. Omitted or empty
    #: means "derive it from the name", which is what every row got before the
    #: form's value was carried at all.
    slug: str | None = Field(default=None, max_length=60)
    description: str = ""
    model: str = ""
    system_prompt: str = ""
    # Omitted means inherit (null), the same as explicit null. It used to mean
    # least privilege — an empty list — and the screen never sends the fields,
    # so every agent made in the UI was born with skills and tools hard-denied:
    # activating a skill on it answered 422 and no tool was ever offered.
    # Least privilege is still one explicit `[]` away for the caller that
    # wants it; a default nobody chose is not a privilege decision.
    tools: list[str] | None = None
    skill_ids: list[str] | None = None
    kinds: list[str] | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)
    color: str = "#5b53e8"
    enabled: bool = True
    visibility: AgentVisibility = AgentVisibility.private


class ToolCatalogOut(Wire):
    name: str
    label: str
    available: bool = True


# ── shares ─────────────────────────────────────────────────────────────
class ShareIn(Wire):
    """Exactly one of the two ids, plus how widely it opens."""

    artifact_id: str | None = None
    session_id: str | None = None
    scope: ShareScope = ShareScope.link


class ShareOut(Wire):
    id: str
    token: str
    artifact_id: str | None
    session_id: str | None
    scope: ShareScope
    views: int
    created_at: datetime

    @classmethod
    def of(cls, s: Share) -> ShareOut:
        return cls.model_validate(s, from_attributes=True)


class BulkDelete(Wire):
    """Ids to remove in one request.

    Capped so one call cannot become a table scan. Ids this account does not
    own are skipped rather than refused — see `_owned_many`.
    """

    ids: list[str] = Field(default_factory=list, max_length=500)


class ShareViewOut(Wire):
    """One visit to a shared link, as the owner sees it.

    `name`/`email` are empty for an anonymous reader and `ip` is empty for a
    signed-in one whose address the proxy did not forward. Both empty is a
    real state — a reader behind a stripping proxy with no account — and it is
    sent as such rather than dressed up as "unknown", because the screen has to
    say plainly that nothing was learned.
    """

    id: str
    at: datetime
    last_at: datetime
    opens: int
    name: str
    email: str
    ip: str
    #: Empty unless a GeoLite2 database is configured, and empty for an
    #: address it does not cover. The screen shows the address alone then,
    #: rather than a guess.
    region: str
    user_agent: str

    @classmethod
    def of(cls, v: ShareView) -> ShareViewOut:
        return cls(
            id=v.id,
            at=v.at,
            last_at=v.last_at,
            opens=v.opens,
            name=v.viewer_name,
            email=v.viewer_email,
            ip=v.ip,
            region=geoip.lookup(v.ip),
            user_agent=v.user_agent,
        )


# ── connectors ─────────────────────────────────────────────────────────
class ConnectorToolOut(Wire):
    name: str
    description: str
    read_only: bool
    enabled: bool

    @classmethod
    def of(cls, t: ConnectorTool) -> ConnectorToolOut:
        return cls.model_validate(t, from_attributes=True)


class ConnectorOut(Wire):
    id: str
    name: str
    slug: str
    description: str
    category: str
    transport: Transport
    #: Endpoint is echoed back, credentials never are.
    endpoint: str
    auth: str
    kinds: JsonList = Field(default_factory=list)
    official: bool
    installed: bool
    enabled: bool
    status: ConnectorStatus
    tools: list[ConnectorToolOut] = Field(default_factory=list)
    last_sync_at: datetime | None
    error: str | None
    #: Credentials this connector holds — **names only, never values**. Read
    #: from the row rather than the catalogue, so a self-registered server can
    #: have its credentials re-entered too.
    env_keys: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, c: Connector, tools: list[ConnectorTool] | None = None) -> ConnectorOut:
        out = cls.model_validate(
            {**c.model_dump(), "auth": c.auth_type}, from_attributes=False
        )
        out.kinds = list(c.kinds or [])
        out.tools = [ConnectorToolOut.of(t) for t in (tools or [])]
        # `{{USER_ID}}`-style placeholders are substituted at spawn time and
        # are not re-entered, so they are excluded.
        out.env_keys = [
            k for k, v in (c.env or {}).items() if not str(v).startswith("{{")
        ]
        return out


class ConnectorIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    category: str = "custom"
    transport: Transport = Transport.stdio
    endpoint: str = ""
    env: dict[str, str] | None = None
    auth: str = "none"
    kinds: list[str] | None = None


class ConnectorPatch(Wire):
    enabled: bool | None = None
    installed: bool | None = None
    endpoint: str | None = None
    env: dict[str, str] | None = None


class InstallRequest(Wire):
    """Credentials for a catalog server, supplied at install time.

    Stored on the connector row and never serialised back — `ConnectorOut` has no
    `env` field, deliberately.
    """

    env: dict[str, str] = Field(default_factory=dict)


class ToolToggle(Wire):
    enabled: bool


class ApiKeyCreate(Wire):
    name: str = Field(min_length=1, max_length=80)


class ApiKeyOut(Wire):
    id: str
    name: str
    #: Last four characters. Enough to tell two keys apart, useless to replay.
    preview: str
    created_at: datetime
    last_used_at: datetime | None = None
    #: Present exactly once, in the response that created it — and set by that
    #: route, never here. Validating straight off the row carried the *stored*
    #: value into every list response: Fernet ciphertext, but ciphertext whose
    #: key derives from `JWT_SECRET`, so it had no business leaving at all.
    secret: str | None = None

    @classmethod
    def of(cls, k: ApiKey) -> ApiKeyOut:
        out = cls.model_validate(k, from_attributes=True)
        out.secret = None
        return out


# ── templates ──────────────────────────────────────────────────────────
class TemplateOut(Wire):
    """A user's starting point, in the shape the gallery already renders.

    Deliberately identical to the built-in `Template` interface in the frontend
    plus `fileId` and `fileName`, so the gallery can concatenate the two lists
    rather than branch on where a card came from.
    """

    id: str
    kind: str
    group: str
    title: str
    description: str
    fills: JsonList = Field(default_factory=list)
    prompt: str
    file_id: str | None = None
    #: Resolved by the router; the row holds only the id. Enough of the file to
    #: render it as an attachment chip without a second round trip — the
    #: composer shows a name, a token count and, when extraction failed, why.
    file_name: str = ""
    file_tokens: int = 0
    file_error: str | None = None
    #: Offered to every account rather than only its author.
    shared: bool = False
    #: Whether the caller may edit or remove it. False on somebody else's
    #: shared template, which the gallery renders without a delete button.
    mine: bool = True
    updated_at: datetime

    @classmethod
    def of(
        cls, t: Template, stored: StoredFile | None = None, *, owner_id: str | None = None
    ) -> TemplateOut:
        out = cls.model_validate(t, from_attributes=True)
        out.fills = list(t.fills or [])
        out.mine = owner_id is None or t.owner_id == owner_id
        if stored is not None:
            out.file_name = stored.name
            out.file_tokens = stored.tokens
            out.file_error = stored.error
        return out


class TemplateIn(Wire):
    kind: str = Field(min_length=1, max_length=20)
    group: str = Field(default="내 템플릿", max_length=40)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=400)
    fills: list[str] | None = None
    prompt: str = Field(default="", max_length=8000)
    file_id: str | None = None
    #: Administrator-only. A non-administrator setting it is refused rather
    #: than silently ignored.
    shared: bool = False


class PromptTemplateOut(Wire):
    """One built-in starting point, in the shape `TemplateOut` already has.

    Every key the two lists share means the same thing in both, so the gallery
    renders one card for a built-in and for a template somebody wrote, and the
    only difference it has to know about is `builtin` — which is what decides
    whether the card offers a delete button.
    """

    id: str
    kind: str
    #: The same surface as `kind`, spelled the way the rendering catalogue
    #: spells it. A starting point is a request rather than a shape, so the two
    #: agree here; the card reads `surface` for both lists rather than reading
    #: `kind` from one and `surface` from the other.
    surface: str
    group: str
    title: str
    description: str
    fills: JsonList = Field(default_factory=list)
    prompt: str
    #: The English half of the same card, empty until it is written. Both sides
    #: travel and the client picks, exactly as on a rendering template.
    title_en: str = ""
    description_en: str = ""
    fills_en: JsonList = Field(default_factory=list)
    prompt_en: str = ""
    #: Ships in the image, so nobody can edit or remove it.
    builtin: bool = True

    @classmethod
    def of(cls, t: PromptTemplate) -> PromptTemplateOut:
        return cls(
            id=t.id,
            kind=t.kind.value,
            surface=t.kind.value,
            group=t.group,
            title=t.title,
            description=t.description,
            fills=list(t.fills),
            prompt=t.prompt,
            title_en=t.title_en,
            description_en=t.description_en,
            fills_en=list(t.fills_en),
            prompt_en=t.prompt_en,
        )


class DesignSystemOut(Wire):
    """A look, in the shape both the project picker and the editor render.

    `tokens` is always complete — a caller should never have to know which of
    the four the row happened to store.
    """

    id: str
    name: str
    description: str
    tokens: dict[str, str] = Field(default_factory=dict)
    body: str
    image_style: str
    craft: JsonList = Field(default_factory=list)
    #: Offered to every account. Administrators only.
    shared: bool = False
    #: Whether the caller may edit or remove it.
    mine: bool = True
    updated_at: datetime

    @classmethod
    def of(cls, d: DesignSystem, *, owner_id: str | None = None) -> DesignSystemOut:
        out = cls.model_validate(d, from_attributes=True)
        out.tokens = design_service.tokens_of(d)
        out.craft = design_service.craft_keys(d.craft)
        out.mine = owner_id is None or d.owner_id == owner_id
        return out


class DesignArgumentOut(Wire):
    """One blank in a media template's prompt, in both languages."""

    name: str
    label: str
    label_en: str = ""
    default: str = ""
    default_en: str = ""
    options: JsonList = Field(default_factory=list)
    options_en: JsonList = Field(default_factory=list)


class DesignExtractIn(Wire):
    """What to read a design system out of: one of the two, not both."""

    #: An uploaded file whose extracted text is already stored.
    file_id: str | None = None
    #: A page, read through the same scraper the `fetch_url` tool uses.
    url: str | None = Field(default=None, max_length=2000)


class DesignExtractOut(Wire):
    """A proposal, not a row. The editor opens on it and the person saves it.

    Shaped like `DesignSystemIn` so the client can hand it straight to the
    form it already has, plus what it cost and what it read.
    """

    name: str
    description: str
    tokens: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    image_style: str = ""
    craft: JsonList = Field(default_factory=list)
    #: What it was read from, so the draft can say so.
    source: str = ""
    credits: int = 0


class DesignTemplateUsageOut(Wire):
    """How often each rendering template was started, by this person and by all.

    Two maps rather than one ordering, because the caller decides how to weigh
    them: the home rail leads with `mine` and falls back to `popular`, and the
    catalogue may want to say something else. An id absent from a map was never
    used, which is not the same as zero and reads the same either way.
    """

    mine: dict[str, int] = Field(default_factory=dict)
    popular: dict[str, int] = Field(default_factory=dict)


class DesignTemplateOut(Wire):
    """One entry of the rendering catalogue.

    Deliberately the same shape the prompt-template gallery already renders —
    title, description, what you have to bring, a starting sentence — plus the
    three things only this catalogue has: which surface it belongs to, whether
    it has a preview to show, and the rules the result will be read against.
    """

    id: str
    kind: str
    surface: str
    name: str
    description: str
    category: str
    fills: JsonList = Field(default_factory=list)
    example_prompt: str
    #: The English half of the same card. Both sides travel; the client picks.
    name_en: str = ""
    description_en: str = ""
    category_en: str = ""
    fills_en: JsonList = Field(default_factory=list)
    example_prompt_en: str = ""
    #: What a review will read the finished thing against, one sentence per
    #: line. The discipline is what actually separates two shapes of the same
    #: kind — 회의록 keeps decisions apart from discussion, 안내문 wants
    #: grounds and an effective date — and it stayed on the server while the
    #: card showed a name and one line.
    #:
    #: Korean only, and deliberately not paired with a `_en` twin: the
    #: checklists are the rubric a Korean critique scores against, and an
    #: English half nobody wrote would be a promise in the wrong language.
    #: Media templates have no checklist and send an empty list.
    checks: JsonList = Field(default_factory=list)
    #: Blanks in `example_prompt`, written `{name}`. Filled in the gallery and
    #: substituted before the sentence reaches the composer, where the person
    #: can still read and change every word of it.
    arguments: list[DesignArgumentOut] = Field(default_factory=list)
    #: Composer settings this template implies — aspect, duration, voice.
    defaults: dict[str, Any] = Field(default_factory=dict)
    #: The extension of the blank form this 서식 ships — `docx`, `pptx`, or
    #: empty where it has none yet.
    #:
    #: The extension rather than a flag, because the button that offers it says
    #: which file is coming. "양식 내려받기" and then a `.pptx` when somebody
    #: expected a `.docx` is a surprise the card could have prevented.
    form_format: str = ""
    #: Whether `/design-templates/{id}/preview` has something to show. The
    #: card decides between a live miniature and its text-only fall-back on
    #: this, instead of loading an iframe that will 404.
    has_preview: bool = False

    @classmethod
    def of(cls, t: object) -> DesignTemplateOut:
        return cls(
            id=t.id,
            kind=t.kind,
            surface=t.surface.value,
            name=t.name,
            description=t.description,
            category=t.category,
            fills=list(t.fills),
            example_prompt=t.example_prompt,
            name_en=t.name_en,
            description_en=t.description_en,
            category_en=t.category_en,
            fills_en=list(t.fills_en),
            example_prompt_en=t.example_prompt_en,
            checks=list(t.checks),
            arguments=[
                DesignArgumentOut(
                    name=a.name,
                    label=a.label,
                    label_en=a.label_en,
                    default=a.default,
                    default_en=a.default_en,
                    options=list(a.options),
                    options_en=list(a.options_en),
                )
                for a in t.arguments
            ],
            defaults=dict(t.defaults),
            form_format=t.form_file.rsplit(".", 1)[-1] if t.form_file else "",
            has_preview=bool(getattr(t, "sample", "") and getattr(t, "seed", "")),
        )


class DesignSystemIn(Wire):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    tokens: dict[str, str] | None = None
    #: Capped rather than free — see `models.workspace.DesignSystem`.
    body: str = Field(default="", max_length=design_service.MAX_BODY)
    image_style: str = Field(default="", max_length=design_service.MAX_IMAGE_STYLE)
    craft: list[str] | None = None
    #: Administrator-only, refused rather than ignored. Same rule as templates.
    shared: bool = False


class KnowledgeUrl(Wire):
    """A page to read into an agent's shelf."""

    url: str = Field(min_length=8, max_length=2000)
