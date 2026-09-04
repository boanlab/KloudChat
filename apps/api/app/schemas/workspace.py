"""Wire shapes for the workspace resources. camelCase out, snake_case in."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

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
from app.services.prompt_templates import PromptTemplate, skill_names

#: Nullable JSONB list column read as a list.
JsonList = Annotated[list[str], BeforeValidator(lambda v: v or [])]
#: Nullable JSONB map column read as a dict.
JsonMap = Annotated[dict[str, str], BeforeValidator(lambda v: v or {})]


# ── files ──────────────────────────────────────────────────────────────
class OpenedDocument(Wire):
    """Id of the document a file was read into."""

    id: str


class FileOut(Wire):
    id: str
    name: str
    size: int
    mime: str
    tokens: int
    project_id: str | None
    session_id: str | None
    #: Set when the file is an agent's knowledge.
    agent_id: str | None = None
    #: Set when the text was ingested from a page rather than uploaded.
    source_url: str | None = None
    #: First 280 characters of the extracted text.
    preview: str = ""
    error: str | None = None
    #: Whether the vector index covers this document.
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
    #: Surface → rendering template id for new sessions in this project.
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
    #: Explicit null clears it; omitted leaves it unchanged (`exclude_unset`).
    design_system_id: str | None = None
    #: Replaced whole, never merged.
    render_templates: dict[str, str] | None = None


# ── artifacts ──────────────────────────────────────────────────────────

#: Characters of section content a listing card carries.
_CARD_CHARS = 400

#: Sections or slides a listing card carries.
_CARD_PARTS = 4


def _card_data(kind: ArtifactKind, data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Trims `data` for a listing card.

    Every key survives; only values shrink. Client renderers read these keys
    unconditionally. Media artifacts travel whole.
    """
    if not data:
        return data
    if kind in (ArtifactKind.html, ArtifactKind.code):
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
            "sources": [],
            "sections": [
                {
                    **{k: v for k, v in (section or {}).items() if k != "content"},
                    "content": str((section or {}).get("content") or "")[:_CARD_CHARS],
                    # Base64 PNGs of rendered mermaid diagrams; emptied, not dropped.
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
    #: True when `data` was trimmed for a listing; fetch by id before rendering or editing.
    partial: bool = False

    @classmethod
    def of(cls, a: Artifact) -> ArtifactOut:
        return cls.model_validate(a, from_attributes=True)

    @classmethod
    def card(cls, a: Artifact) -> ArtifactOut:
        """Listing row with `data` trimmed by `_card_data`."""
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


class ArtifactVersionDetailOut(ArtifactVersionOut):
    data: dict[str, Any] | None = None

    @classmethod
    def of(cls, v: object) -> ArtifactVersionDetailOut:
        return cls.model_validate(v, from_attributes=True)


class ArtifactRestore(Wire):
    version: int


class ArtifactPatch(Wire):
    title: str | None = None
    data: dict[str, Any] | None = None
    project_id: str | None = None
    #: One line describing the edit, stored with the superseded version.
    summary: str = ""
    #: Version the editor started from. Sent: a mismatch is refused. Omitted: no check.
    expected_version: int | None = None


class SlideFactCheck(Wire):
    """Which slide to check."""

    slide_id: str


class DiagramPicture(Wire):
    """A browser-rendered mermaid diagram to store.

    `key` is `report_export.diagram_key` of the diagram's source.
    """

    section_id: str
    key: str = Field(min_length=8, max_length=64)
    #: A `data:` URL; anything else is refused.
    src: str = Field(max_length=4_000_000)


class SectionFactCheck(Wire):
    """Which report section to check."""

    section_id: str


class SlideImage(Wire):
    """Places an existing `image` artifact on one slide."""

    slide_id: str = Field(min_length=1, max_length=64)
    artifact_id: str = Field(min_length=1, max_length=64)
    caption: str = Field(default="", max_length=200)


class BlockImage(Wire):
    """Places an existing `image` artifact into one block of an HTML page."""

    index: int = Field(ge=0, le=63)
    artifact_id: str = Field(min_length=1, max_length=64)
    #: Empty leaves the figure uncaptioned.
    caption: str = Field(default="", max_length=200)


class SectionImage(Wire):
    """Appends an existing `image` artifact to one report section as a Markdown image line."""

    section_id: str = Field(min_length=1, max_length=64)
    artifact_id: str = Field(min_length=1, max_length=64)
    #: Empty leaves the figure uncaptioned.
    caption: str = Field(default="", max_length=200)


class BlockRewrite(Wire):
    """Which block of an HTML artifact to rewrite; blocks are addressed by position."""

    index: int = Field(ge=0, le=63)
    #: What to change; empty means retry.
    note: str = Field(default="", max_length=600)


class SectionRewrite(Wire):
    """Which report section to rewrite."""

    section_id: str
    #: What to change; empty means retry.
    note: str = ""


class SlideRewrite(Wire):
    """Which slide to rewrite."""

    slide_id: str
    #: What to change; empty means retry.
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
    visibility: Visibility = Visibility.private
    installs: int = 0
    #: The shared row this one was copied from.
    origin_id: str | None = None
    updated_at: datetime

    @classmethod
    def of(cls, s: Skill) -> SkillOut:
        out = cls.model_validate(s, from_attributes=True)
        out.kinds = list(s.kinds or [])
        out.required_tools = list(s.required_tools or [])
        return out


class StoreSkillOut(SkillOut):
    """A skill as the store lists it."""

    owner_id: str
    owner_name: str = ""
    #: Published by an administrator.
    official: bool = False
    #: The caller already holds a copy.
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
    guide: str = ""
    starters: JsonList = Field(default_factory=list)
    share_mode: Literal["open", "sealed"] = "open"
    #: `system_prompt` is withheld: a sealed original seen by a non-owner, or a copy of one.
    sealed: bool = False
    temperature: float
    color: str
    enabled: bool
    visibility: AgentVisibility
    installs: int
    catalog_key: str | None = None
    origin_id: str | None = None
    #: Published by an administrator. Store-only.
    official: bool = False
    #: The caller already holds a copy. Store-only.
    installed: bool = False
    runs: int
    #: The agent has readable knowledge, so `search_knowledge` can be offered.
    has_knowledge: bool = False
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
        #: Caller id; a sealed original's prompt is withheld from non-owners.
        viewer_id: str | None = None,
    ) -> AgentOut:
        out = cls.model_validate(a, from_attributes=True)
        out.owner_name = owner_name
        out.has_knowledge = has_knowledge
        out.official = official
        out.installed = installed
        out.tools = None if a.tools is None else list(a.tools)
        out.skill_ids = None if a.skill_ids is None else list(a.skill_ids)
        out.kinds = list(a.kinds or [])
        out.starters = [str(x) for x in (a.starters or []) if str(x).strip()]
        out.share_mode = "sealed" if a.share_mode == "sealed" else "open"
        # A sealed prompt never leaves the server except to its owner.
        withheld = a.share_mode == "sealed" and viewer_id is not None and a.owner_id != viewer_id
        if a.sealed or withheld:
            out.system_prompt = ""
            out.sealed = True
        return out


class AgentIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    #: The @handle, slugified server-side, unique per owner. Empty derives it from the name.
    slug: str | None = Field(default=None, max_length=60)
    description: str = ""
    model: str = ""
    system_prompt: str = ""
    #: Null (or omitted) inherits every tool/skill; `[]` denies all.
    tools: list[str] | None = None
    skill_ids: list[str] | None = None
    kinds: list[str] | None = None
    guide: str = Field(default="", max_length=2000)
    starters: list[str] = Field(default_factory=list, max_length=6)
    share_mode: Literal["open", "sealed"] = "open"
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
    """Exactly one of `artifact_id` / `session_id`."""

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
    """Ids to delete in one request; ids the caller does not own are skipped."""

    ids: list[str] = Field(default_factory=list, max_length=500)


class ShareViewOut(Wire):
    """One visit to a shared link.

    `name`/`email` are empty for an anonymous reader; `ip` may be empty.
    """

    id: str
    at: datetime
    last_at: datetime
    opens: int
    name: str
    email: str
    ip: str
    #: Empty without a GeoLite2 database or for an address it does not cover.
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
    #: Credential names only, never values.
    env_keys: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, c: Connector, tools: list[ConnectorTool] | None = None) -> ConnectorOut:
        out = cls.model_validate({**c.model_dump(), "auth": c.auth_type}, from_attributes=False)
        out.kinds = list(c.kinds or [])
        out.tools = [ConnectorToolOut.of(t) for t in (tools or [])]
        # `{{...}}` placeholders are substituted at spawn time, not re-entered.
        out.env_keys = [k for k, v in (c.env or {}).items() if not str(v).startswith("{{")]
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
    """Credentials for a catalog server; stored on the row, never serialised back."""

    env: dict[str, str] = Field(default_factory=dict)


class ToolToggle(Wire):
    enabled: bool


class ApiKeyCreate(Wire):
    name: str = Field(min_length=1, max_length=80)


class ApiKeyOut(Wire):
    id: str
    name: str
    #: Last four characters of the key.
    preview: str
    created_at: datetime
    last_used_at: datetime | None = None
    #: Set only by the create route, in that one response; `of()` always clears it.
    secret: str | None = None

    @classmethod
    def of(cls, k: ApiKey) -> ApiKeyOut:
        out = cls.model_validate(k, from_attributes=True)
        out.secret = None
        return out


# ── templates ──────────────────────────────────────────────────────────
class TemplateOut(Wire):
    """A user-written starting point.

    Same shape as the frontend's built-in `Template` plus the file fields.
    """

    id: str
    kind: str
    group: str
    title: str
    description: str
    fills: JsonList = Field(default_factory=list)
    #: One example per blank, in `fills` order.
    examples: JsonList = Field(default_factory=list)
    needs: JsonList = Field(default_factory=list)
    prompt: str
    file_id: str | None = None
    #: Resolved by the router from `file_id`.
    file_name: str = ""
    file_tokens: int = 0
    file_error: str | None = None
    #: Empty lets the surface choose a 서식.
    render_template_id: str = ""
    #: Offered to every account.
    shared: bool = False
    #: Whether the caller may edit or remove it.
    mine: bool = True
    updated_at: datetime

    @classmethod
    def of(
        cls, t: Template, stored: StoredFile | None = None, *, owner_id: str | None = None
    ) -> TemplateOut:
        out = cls.model_validate(t, from_attributes=True)
        out.fills = list(t.fills or [])
        out.examples = list(t.examples or [])
        out.needs = list(t.needs or [])
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
    #: One example per blank, in `fills` order.
    examples: list[str] | None = None
    #: `web`, `file`.
    needs: list[str] | None = None
    prompt: str = Field(default="", max_length=8000)
    file_id: str | None = None
    #: Validated against the rendering catalogue by the router.
    render_template_id: str = Field(default="", max_length=60)
    #: Administrator-only; refused otherwise.
    shared: bool = False


class PromptTemplateOut(Wire):
    """A built-in starting point, in the shape of `TemplateOut`."""

    id: str
    kind: str
    #: Same value as `kind`; the card reads `surface` for both catalogues.
    surface: str
    group: str
    title: str
    description: str
    fills: JsonList = Field(default_factory=list)
    prompt: str
    #: English half; both languages travel and the client picks.
    title_en: str = ""
    description_en: str = ""
    fills_en: JsonList = Field(default_factory=list)
    prompt_en: str = ""
    #: Empty lets the surface choose a 서식.
    render_template_id: str = ""
    #: One example per blank, in `fills` order.
    examples: JsonList = Field(default_factory=list)
    examples_en: JsonList = Field(default_factory=list)
    #: `web`, `file`.
    needs: JsonList = Field(default_factory=list)
    #: Workspace skills to activate for the turn, by name.
    skills: JsonList = Field(default_factory=list)
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
            render_template_id=t.render_template_id,
            examples=list(t.examples),
            examples_en=list(t.examples_en),
            needs=list(t.needs),
            # Names on the wire, keys in the catalogue.
            skills=skill_names(t.skills),
        )


class DesignSystemOut(Wire):
    """A design system. `tokens` is always complete."""

    id: str
    name: str
    description: str
    tokens: dict[str, str] = Field(default_factory=dict)
    body: str
    image_style: str
    craft: JsonList = Field(default_factory=list)
    #: Offered to every account.
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
    #: A paragraph field rather than a one-line one.
    long: bool = False


class DesignExtractIn(Wire):
    """Source to extract a design system from: exactly one of the two."""

    file_id: str | None = None
    #: Read through the same scraper as the `fetch_url` tool.
    url: str | None = Field(default=None, max_length=2000)


class DesignExtractOut(Wire):
    """An extracted design-system draft, shaped like `DesignSystemIn`; not stored."""

    name: str
    description: str
    tokens: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    image_style: str = ""
    craft: JsonList = Field(default_factory=list)
    #: What it was read from.
    source: str = ""
    credits: int = 0


class DesignTemplateUsageOut(Wire):
    """Rendering template use counts by id, for the caller and for everyone."""

    mine: dict[str, int] = Field(default_factory=dict)
    popular: dict[str, int] = Field(default_factory=dict)


class DesignTemplateOut(Wire):
    """One entry of the rendering catalogue."""

    id: str
    kind: str
    surface: str
    name: str
    description: str
    category: str
    fills: JsonList = Field(default_factory=list)
    example_prompt: str
    #: English half; both languages travel and the client picks.
    name_en: str = ""
    description_en: str = ""
    category_en: str = ""
    fills_en: JsonList = Field(default_factory=list)
    example_prompt_en: str = ""
    #: Review checklist, one sentence per line, Korean only. Empty for media templates.
    checks: JsonList = Field(default_factory=list)
    #: Blanks in `example_prompt`, written `{name}`.
    arguments: list[DesignArgumentOut] = Field(default_factory=list)
    #: Composer settings this template implies: aspect, duration, voice.
    defaults: dict[str, Any] = Field(default_factory=dict)
    #: `method`, `flow` or `concept` for a diagram 서식; empty for a picture.
    figure: str = ""
    #: Extension of the blank Office form (`docx`, `pptx`), or empty.
    form_format: str = ""
    #: Whether `/design-templates/{id}/preview` has something to show.
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
            figure=getattr(t, "figure", "") or "",
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
                    long=bool(getattr(a, "long", False)),
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
    body: str = Field(default="", max_length=design_service.MAX_BODY)
    image_style: str = Field(default="", max_length=design_service.MAX_IMAGE_STYLE)
    craft: list[str] | None = None
    #: Administrator-only; refused otherwise.
    shared: bool = False


class KnowledgeUrl(Wire):
    """A page to read into an agent's knowledge."""

    url: str = Field(min_length=8, max_length=2000)
