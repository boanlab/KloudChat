"""Workspace wire shapes; camelCase out, snake_case in."""

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
    Transport,
    Visibility,
)
from app.schemas.auth import Wire
from app.services import design as design_service
from app.services import design_templates, geoip, settings_store, skill_packages

JsonList = Annotated[list[str], BeforeValidator(lambda v: v or [])]
JsonMap = Annotated[dict[str, str], BeforeValidator(lambda v: v or {})]


class OpenedDocument(Wire):
    """Id of the document the file was read into."""

    id: str


class FileOut(Wire):
    id: str
    name: str
    size: int
    mime: str
    tokens: int
    project_id: str | None
    session_id: str | None
    agent_id: str | None = None
    source_url: str | None = None
    preview: str = ""
    error: str | None = None
    indexed: bool = False
    #: OCR: null | "queued" | "running" | "done" | "failed", with page counts
    ocr_state: str | None = None
    ocr_total: int = 0
    ocr_done: int = 0
    ocr_review: int = 0
    created_at: datetime

    @classmethod
    def of(cls, f: StoredFile) -> FileOut:
        out = cls.model_validate(f, from_attributes=True)
        out.preview = f.text[:280]
        out.indexed = f.indexed_at is not None
        return out


class ProjectOut(Wire):
    id: str
    name: str
    description: str
    emoji: str
    instructions: str
    skill_ids: JsonList = Field(default_factory=list)
    design_system_id: str | None = None
    #: surface → template id for new sessions
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
    #: explicit null clears; omitted unchanged (`exclude_unset`)
    design_system_id: str | None = None
    render_templates: dict[str, str] | None = None


_CARD_CHARS = 400

_CARD_PARTS = 4


def _card_data(kind: ArtifactKind, data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Listing-card `data`: all keys kept (renderers read them), values shrunk; media whole."""
    if not data:
        return data
    if kind in (ArtifactKind.html, ArtifactKind.code):
        return {**data, "content": "", "blocks": []}
    if kind is ArtifactKind.deck:
        return {
            **data,
            # few carried, all counted
            "partCount": len(data.get("slides") or []),
            "slides": [
                {k: v for k, v in (slide or {}).items() if k in ("id", "title", "layout")}
                for slide in (data.get("slides") or [])[:_CARD_PARTS]
            ],
        }
    if kind is ArtifactKind.report:
        return {
            **data,
            "partCount": len(data.get("sections") or []),
            "sources": [],
            "sections": [
                {
                    **{k: v for k, v in (section or {}).items() if k != "content"},
                    "content": str((section or {}).get("content") or "")[:_CARD_CHARS],
                    # base64 mermaid PNGs emptied, key kept
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
    #: trimmed listing `data`; fetch by id before render/edit
    partial: bool = False

    @classmethod
    def of(cls, a: Artifact) -> ArtifactOut:
        return cls.model_validate(a, from_attributes=True)

    @classmethod
    def card(cls, a: Artifact) -> ArtifactOut:
        """Listing row; `data` trimmed by `_card_data`."""
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
    #: edit summary, stored with superseded version
    summary: str = ""
    #: optimistic check: mismatch refused; omitted: no check
    expected_version: int | None = None


class SlideFactCheck(Wire):
    """Which slide to check."""

    slide_id: str


class DiagramPicture(Wire):
    """Browser-rendered mermaid PNG; `key` = `report_export.diagram_key`."""

    section_id: str
    key: str = Field(min_length=8, max_length=64)
    #: `data:` URL only
    src: str = Field(max_length=4_000_000)


class SectionFactCheck(Wire):
    """Which report section to check."""

    section_id: str


class SlideDiagramPicture(Wire):
    """Browser raster of a deck's own figure; stale `key` refused."""

    slide_id: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=8, max_length=64)
    #: `data:` URL only
    src: str = Field(min_length=1, max_length=6_000_000)


class SlideImage(Wire):
    """Places an existing `image` artifact on one slide."""

    slide_id: str = Field(min_length=1, max_length=64)
    artifact_id: str = Field(min_length=1, max_length=64)
    caption: str = Field(default="", max_length=200)


class BlockImage(Wire):
    """Places an existing `image` artifact into one block of an HTML page."""

    index: int = Field(ge=0, le=63)
    artifact_id: str = Field(min_length=1, max_length=64)
    #: empty: no caption
    caption: str = Field(default="", max_length=200)


class SectionImage(Wire):
    """Appends an `image` artifact to a report section as Markdown."""

    section_id: str = Field(min_length=1, max_length=64)
    artifact_id: str = Field(min_length=1, max_length=64)
    #: empty: no caption
    caption: str = Field(default="", max_length=200)


class BlockRewrite(Wire):
    """HTML block to rewrite, by position."""

    index: int = Field(ge=0, le=63)
    #: change request; empty = retry
    note: str = Field(default="", max_length=600)


class SectionRewrite(Wire):
    """Which report section to rewrite."""

    section_id: str
    #: change request; empty = retry
    note: str = ""


class SlideRewrite(Wire):
    """Which slide to rewrite."""

    slide_id: str
    #: change request; empty = retry
    note: str = ""


class SkillServer(Wire):
    """Server-run skill fields (`services/skill_specs.py`); shipped scripts only."""

    #: `pre`/`post` attachable check; `entry` = shipped check
    stage: str = ""
    entry: str = ""
    #: shipped `<key>/<script>` over chat answers
    post_check: list[str] = Field(default_factory=list, max_length=8)
    #: True, or judge focus
    post_judge: bool | str = False
    copy_check: bool = False
    copy_kinds: list[str] = Field(default_factory=list, max_length=12)
    tables: bool = False
    own_stage: bool = False
    #: `run_skill_script` targets: shipped `<key>/<script>` or own scripts (sandbox)
    uses: list[str] = Field(default_factory=list, max_length=12)


class SkillScriptOut(Wire):
    name: str
    summary: str = ""
    size: int = 0
    #: owner only; empty otherwise
    source: str = ""


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
    origin_id: str | None = None
    #: a copy: who shared the skill it came from ("" when gone or not a copy)
    origin_owner_name: str = ""
    updated_at: datetime
    #: package's fixed scripts; empty for plain skill
    scripts: list[str] = Field(default_factory=list)
    #: shipped copy with none stored shows package's
    server: SkillServer = Field(default_factory=SkillServer)
    #: own scripts (sandbox); `source` owner-only
    script_files: list[SkillScriptOut] = Field(default_factory=list)
    #: "" · pending · approved · rejected
    review: str = ""
    review_note: str = ""
    #: False for part-only shipped skills (`listed: false`) and copies; still runnable by name
    listed: bool = True

    @classmethod
    def of(cls, s: Skill, *, owner: bool = True) -> SkillOut:
        out = cls._base(s)
        out.server = _server_of(s)
        out.script_files = [SkillScriptOut(
            name=str(f.get("name") or ""), summary=str(f.get("summary") or ""),
            size=int(f.get("size") or 0), source=str(f.get("source") or "") if owner else "")
            for f in s.script_files or [] if isinstance(f, dict)]
        return out

    @classmethod
    def _base(cls, s: Skill):
        names = ("id", "name", "slug", "description", "when_to_use", "body", "catalog_key",
                 "source", "kinds", "required_tools", "estimated_tokens", "version", "enabled",
                 "visibility", "installs", "origin_id", "updated_at", "review", "review_note",
                 "owner_id")
        out = cls.model_validate({k: getattr(s, k) for k in names if k in cls.model_fields})
        out.kinds = list(s.kinds or [])
        out.required_tools = list(s.required_tools or [])
        out.scripts = skill_packages.script_names(s.catalog_key)
        out.listed = skill_packages.listed(s.catalog_key)
        return out


def _server_of(s: Skill) -> SkillServer:
    from app.services import skill_specs

    fields = skill_specs.of_row(s).fields()
    return SkillServer.model_validate(fields)


class StoreSkillOut(SkillOut):
    """Store listing entry."""

    owner_id: str
    owner_name: str = ""
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
        out = cls._base(s)
        out.server = _server_of(s)
        # metadata only; code visible to a copy's owner
        out.script_files = [SkillScriptOut(name=str(f.get("name") or ""),
                                           summary=str(f.get("summary") or ""),
                                           size=int(f.get("size") or 0))
                            for f in s.script_files or [] if isinstance(f, dict)]
        out.owner_name = owner_name
        out.official = official
        out.installed = installed
        return out


class SkillIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    when_to_use: str = Field(default="", max_length=2_000)
    #: page or two; more crowds out the conversation
    body: str = Field(default="", max_length=24_000)
    kinds: list[str] | None = None
    required_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    visibility: Visibility = Visibility.private
    #: validated against shipped allowlists
    server: SkillServer | None = None


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
    #: bindings; see `Agent.surface` onward
    surface: str | None = None
    render_template_id: str | None = None
    doc_format: str | None = None
    design_system_id: str | None = None
    #: lets viewers without row access find their same-name design
    design_name: str = ""
    #: 담당 부서·연락처, said verbatim
    contact: str = ""
    guide: str = ""
    starters: JsonList = Field(default_factory=list)
    share_mode: Literal["open", "sealed"] = "open"
    #: prompt withheld (sealed original for non-owner, or its copy)
    sealed: bool = False
    temperature: float
    color: str
    enabled: bool
    visibility: AgentVisibility
    installs: int
    catalog_key: str | None = None
    origin_id: str | None = None
    pipeline_id: str | None = None
    base_key: str | None = None
    #: out of catalogue; kept for past conversations
    retired: bool = False
    check_skills: list[str] | None = None
    check_options: dict | None = None
    bundles: list | None = None
    #: own 빠진 정보 질문; null: base's
    gap_questions: list | None = None
    #: 서식's questions, asked alongside own
    inherited_questions: list = Field(default_factory=list)
    #: base's questions when none stored; form seed
    base_questions: list = Field(default_factory=list)
    #: 진행 과정 (`bases.process_of`); `steps` owner-only
    process: dict | None = None
    #: own 진행 과정 설정; null: 서식's
    process_settings: dict | None = None
    #: 서식 defaults shown in the form
    inherited_settings: dict = Field(default_factory=dict)
    official: bool = False
    installed: bool = False
    runs: int
    #: readable knowledge → `search_knowledge` offered
    has_knowledge: bool = False
    #: owner's published bot `{id, slug, status}`
    publication: dict | None = None
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
        design_name: str = "",
        #: caller id; sealed prompt withheld from non-owners
        viewer_id: str | None = None,
        process: dict | None = None,
        publication: dict | None = None,
    ) -> AgentOut:
        out = cls.model_validate(a, from_attributes=True)
        out.process = process
        out.publication = publication
        out.check_skills = None if a.check_skills is None else [str(k) for k in a.check_skills]
        out.check_options = dict(a.check_options) if a.check_options else None
        out.gap_questions = None if a.gap_questions is None else [dict(q) for q in a.gap_questions]
        out.inherited_questions = _inherited_questions(a)
        out.process_settings = None if a.process_settings is None else dict(a.process_settings)
        out.inherited_settings = _inherited_settings(a)
        out.base_questions = _base_questions(a)
        out.owner_name = owner_name
        out.design_name = design_name
        out.has_knowledge = has_knowledge
        out.official = official
        out.installed = installed
        out.tools = None if a.tools is None else list(a.tools)
        out.skill_ids = None if a.skill_ids is None else list(a.skill_ids)
        out.kinds = list(a.kinds or [])
        out.starters = [str(x) for x in (a.starters or []) if str(x).strip()]
        out.share_mode = "sealed" if a.share_mode == "sealed" else "open"
        # sealed prompt only to its owner
        withheld = a.share_mode == "sealed" and viewer_id is not None and a.owner_id != viewer_id
        if a.sealed or withheld:
            out.system_prompt = ""
            out.sealed = True
        if viewer_id is not None and a.owner_id != viewer_id:
            out.pipeline_id = None  # the owner's own binding
        return out


class AgentIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    #: @handle, slugified, unique per owner; empty: from name
    slug: str | None = Field(default=None, max_length=60)
    description: str = ""
    model: str = ""
    #: persona-sized, bounded to protect the window
    system_prompt: str = Field(default="", max_length=24_000)
    #: null inherits all tools/skills; `[]` denies all
    tools: list[str] | None = None
    skill_ids: list[str] | None = None
    kinds: list[str] | None = None
    #: null: first of `kinds` (chat if unrestricted); else one of `kinds`
    surface: str | None = Field(default=None, max_length=20)
    #: `/design-templates` 서식 on one of `kinds`
    render_template_id: str | None = Field(default=None, max_length=60)
    #: `doc_formats` id when 서식 fixes none
    doc_format: str | None = Field(default=None, max_length=40)
    design_system_id: str | None = Field(default=None, max_length=64)
    #: 담당 부서·연락처 (「학사지원팀 02-760-1234, haksa@example.ac.kr」), said verbatim
    contact: str = Field(default="", max_length=300)
    guide: str = Field(default="", max_length=2000)
    starters: list[str] = Field(default_factory=list, max_length=6)
    share_mode: Literal["open", "sealed"] = "open"
    temperature: float = Field(default=0.7, ge=0, le=2)
    color: str = Field(default="#5b53e8", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True
    visibility: AgentVisibility = AgentVisibility.private
    #: server-run check skills (`app/skills` with `stage`)
    check_skills: list[str] | None = Field(default=None, max_length=12)
    #: `grounding-check` strictness `{tool_numbers, titles}`
    check_options: dict[str, bool] | None = None
    #: validated on save; null: none stored
    gap_questions: list[dict[str, Any]] | None = Field(default=None, max_length=12)
    #: validated on save; null: 서식's
    process_settings: dict[str, Any] | None = None


def _inherited_settings(a: Agent) -> dict:
    """Settings of its 서식 (format-bound or named user template), as form defaults."""
    from app.services import process_settings

    return process_settings.resolve(template_id=a.render_template_id,
                                    format_id=a.doc_format).wire()


def _inherited_questions(a: Agent) -> list[dict]:
    """Questions of its 서식 (or the format-bound one)."""
    from app.services import gap_questions

    return gap_questions.resolve(own=[], template_id=a.render_template_id,
                                 doc_format=a.doc_format)


def _base_questions(a: Agent) -> list[dict]:
    """Base official agent's questions when none stored."""
    from app.services import gap_questions

    if a.gap_questions is not None:
        return []
    shown = {q["id"] for q in _inherited_questions(a)}
    return [q for q in gap_questions.resolve(own=None, base_key=a.catalog_key or a.base_key)
            if q["id"] not in shown]


class ToolCatalogOut(Wire):
    name: str
    label: str
    available: bool = True
    #: 「기본 도구」 or connector name; picker grouping
    group: str = ""
    title: str = ""
    description: str = ""


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
    """Bulk delete; unowned ids skipped."""

    ids: list[str] = Field(default_factory=list, max_length=500)


class ShareViewOut(Wire):
    """Shared-link visit; name/email empty if anonymous, ip may be empty."""

    id: str
    at: datetime
    last_at: datetime
    opens: int
    name: str
    email: str
    ip: str
    #: empty if no GeoLite2 coverage
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
    #: credential names only
    env_keys: list[str] = Field(default_factory=list)
    #: 기본 제공 copy: can be switched off, not removed
    by_default: bool = False

    @classmethod
    def of(cls, c: Connector, tools: list[ConnectorTool] | None = None) -> ConnectorOut:
        out = cls.model_validate({**c.model_dump(), "auth": c.auth_type}, from_attributes=False)
        out.kinds = list(c.kinds or [])
        out.tools = [ConnectorToolOut.of(t) for t in (tools or [])]
        # `{{...}}` placeholders filled at spawn, not user-entered
        out.env_keys = [
            k for k, v in settings_store.decrypt_env(c.env).items() if not v.startswith("{{")
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
    """Catalog-server credentials; stored, never serialised back."""

    env: dict[str, str] = Field(default_factory=dict)


class ToolToggle(Wire):
    enabled: bool


class ApiKeyCreate(Wire):
    name: str = Field(min_length=1, max_length=80)


class ApiKeyOut(Wire):
    id: str
    name: str
    preview: str
    created_at: datetime
    last_used_at: datetime | None = None
    #: create response only; `of()` clears it
    secret: str | None = None

    @classmethod
    def of(cls, k: ApiKey) -> ApiKeyOut:
        out = cls.model_validate(k, from_attributes=True)
        out.secret = None
        return out


class DesignSystemOut(Wire):
    """Design system; `tokens` always complete."""

    id: str
    name: str
    description: str
    tokens: dict[str, str] = Field(default_factory=dict)
    body: str
    image_style: str
    craft: JsonList = Field(default_factory=list)
    shared: bool = False
    mine: bool = True
    #: False for legacy shipped themes
    listed: bool = True
    updated_at: datetime

    @classmethod
    def of(cls, d: DesignSystem, *, owner_id: str | None = None,
           catalog_owner: str | None = None) -> DesignSystemOut:
        out = cls.model_validate(d, from_attributes=True)
        out.tokens = design_service.tokens_of(d)
        out.craft = design_service.craft_keys(d.craft)
        out.mine = owner_id is None or d.owner_id == owner_id
        from app.services import starter

        out.listed = starter.design_listed(d.name, d.owner_id, catalog_owner)
        return out


class DesignExtractIn(Wire):
    """Extraction source: exactly one."""

    file_id: str | None = None
    #: via the `fetch_url` scraper
    url: str | None = Field(default=None, max_length=2000)


class DesignExtractOut(Wire):
    """Extracted draft shaped like `DesignSystemIn`; not stored."""

    name: str
    description: str
    tokens: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    image_style: str = ""
    craft: JsonList = Field(default_factory=list)
    source: str = ""
    credits: int = 0


class DesignTemplateUsageOut(Wire):
    """Template use counts: caller's and global."""

    mine: dict[str, int] = Field(default_factory=dict)
    popular: dict[str, int] = Field(default_factory=dict)


class DesignTemplateOut(Wire):
    """Rendering catalogue entry."""

    id: str
    kind: str
    surface: str
    name: str
    description: str
    category: str
    fills: JsonList = Field(default_factory=list)
    example_prompt: str
    #: English fields; client picks the language
    name_en: str = ""
    description_en: str = ""
    category_en: str = ""
    fills_en: JsonList = Field(default_factory=list)
    example_prompt_en: str = ""
    #: Korean review checklist, one per line
    checks: JsonList = Field(default_factory=list)
    #: blank Office form extension, or empty
    form_format: str = ""
    has_preview: bool = False
    #: deck sample slides (`/preview?slide=N`); 0 otherwise
    slide_count: int = 0
    #: user template (`ut-<id>`), own or shared
    user: bool = False
    mine: bool = False
    #: False: chosen by id but not offered
    listed: bool = True

    @classmethod
    def of(cls, t: object, viewer_id: str = "") -> DesignTemplateOut:
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
            form_format=t.form_file.rsplit(".", 1)[-1] if t.form_file else "",
            has_preview=bool(getattr(t, "sample", "") and getattr(t, "seed", "")),
            slide_count=(
                len(design_templates.sample_slides(t))
                if t.kind == "deck" and getattr(t, "sample", "")
                else 0
            ),
            user=bool(getattr(t, "owner_id", "")),
            mine=bool(viewer_id) and getattr(t, "owner_id", "") == viewer_id,
            listed=bool(getattr(t, "listed", True)),
        )


class DesignSystemIn(Wire):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    tokens: dict[str, str] | None = None
    body: str = Field(default="", max_length=design_service.MAX_BODY)
    image_style: str = Field(default="", max_length=design_service.MAX_IMAGE_STYLE)
    craft: list[str] | None = None
    #: admin-only
    shared: bool = False


class KnowledgeUrl(Wire):
    """Page to ingest into agent knowledge."""

    url: str = Field(min_length=8, max_length=2000)
