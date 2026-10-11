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
    Transport,
    Visibility,
)
from app.schemas.auth import Wire
from app.services import design as design_service
from app.services import design_templates, geoip, settings_store, skill_packages

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
    #: Scanned pages being read: null when none were needed, else
    #: "queued" | "running" | "done" | "failed", with pages to read, read, and to check.
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


class SlideDiagramPicture(Wire):
    """The browser's raster of a figure the deck drew for itself (`slide["diagram"]`).

    `key` is `report_export.diagram_key` of the diagram's source; a stale key is refused.
    """

    slide_id: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=8, max_length=64)
    #: A `data:` URL; anything else is refused.
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
class SkillServer(Wire):
    """What the server runs for a skill (`services/skill_specs.py`), each naming shipped
    scripts only (`GET /skills/server-catalogue` lists them)."""

    #: `pre`/`post`: a check an agent can attach; `entry` is the shipped check it runs.
    stage: str = ""
    entry: str = ""
    #: Shipped scripts run over the skill's chat answers, `<key>/<script>`.
    post_check: list[str] = Field(default_factory=list, max_length=8)
    #: True, or what the judge looks at in particular.
    post_judge: bool | str = False
    copy_check: bool = False
    copy_kinds: list[str] = Field(default_factory=list, max_length=12)
    tables: bool = False
    own_stage: bool = False
    #: Scripts its instructions run through `run_skill_script`: shipped `<key>/<script>` or
    #: the skill's own script names (sandbox only).
    uses: list[str] = Field(default_factory=list, max_length=12)


class SkillScriptOut(Wire):
    name: str
    summary: str = ""
    size: int = 0
    #: The text itself, for its owner; empty for anyone else.
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
    #: The shared row this one was copied from.
    origin_id: str | None = None
    updated_at: datetime
    #: Fixed scripts its package ships (`run_skill_script`); empty for a plain skill.
    scripts: list[str] = Field(default_factory=list)
    #: What the server runs for it; a shipped copy that stores none shows its package's.
    server: SkillServer = Field(default_factory=SkillServer)
    #: Its own scripts (sandbox only); `source` only for the owner.
    script_files: list[SkillScriptOut] = Field(default_factory=list)
    #: Sharing with scripts: "" · pending · approved · rejected.
    review: str = ""
    review_note: str = ""
    #: Shown in the lists people pick from; False for a shipped skill kept only as a part
    #: (`listed: false` in its SKILL.md), its copies included. It still runs by name.
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
        out = cls._base(s)
        out.server = _server_of(s)
        # The store shows what a copy would run, not the code: a copy shows it to its owner.
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
    #: A skill is a page or two of instructions; past this it crowds the conversation out.
    body: str = Field(default="", max_length=24_000)
    kinds: list[str] | None = None
    required_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    visibility: Visibility = Visibility.private
    #: What the server runs for it; validated against the shipped allowlists.
    server: SkillServer | None = None


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
    #: Bindings; see `Agent.surface` and the fields after it.
    surface: str | None = None
    render_template_id: str | None = None
    doc_format: str | None = None
    design_system_id: str | None = None
    #: Name of that design, so a viewer who cannot see the row finds their own of that name.
    design_name: str = ""
    #: 담당 부서·연락처: said as written when the material does not answer.
    contact: str = ""
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
    #: The owner's user pipeline this agent's turns run (`PUT /agents/{id}/process`).
    pipeline_id: str | None = None
    #: The official process a person's own agent starts from (`services/bases.py`).
    base_key: str | None = None
    #: Taken out of the catalogue; never listed, kept for the conversations made with it.
    retired: bool = False
    #: Check skills run around its writing, and how strict the trace is (`Agent.check_skills`).
    check_skills: list[str] | None = None
    check_options: dict | None = None
    #: The 묶음 ticked (`Agent.bundles`).
    bundles: list | None = None
    #: 빠진 정보 질문 of its own (`Agent.gap_questions`); null asks what its base asks.
    gap_questions: list | None = None
    #: The questions its 서식 (or the 서식 of its format) asks — shown in the form, asked
    #: alongside its own.
    inherited_questions: list = Field(default_factory=list)
    #: With none of its own stored, what its base asks instead: the form starts its own list
    #: from these.
    base_questions: list = Field(default_factory=list)
    #: 진행 과정: `{mode, base, extras, chain, review, needsReview, steps?}`
    #: (`bases.process_of`); `steps` for the owner only.
    process: dict | None = None
    #: 진행 과정 설정 of its own (`Agent.process_settings`); null follows its 서식's.
    process_settings: dict | None = None
    #: What its 서식 (or the 서식 of its format) sets: the form shows these as the defaults.
    inherited_settings: dict = Field(default_factory=dict)
    #: Published by an administrator. Store-only.
    official: bool = False
    #: The caller already holds a copy. Store-only.
    installed: bool = False
    runs: int
    #: The agent has readable knowledge, so `search_knowledge` can be offered.
    has_knowledge: bool = False
    #: The owner's published bot of this agent (`publications`): `{id, slug, status}`.
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
        #: Caller id; a sealed original's prompt is withheld from non-owners.
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
        # A sealed prompt never leaves the server except to its owner.
        withheld = a.share_mode == "sealed" and viewer_id is not None and a.owner_id != viewer_id
        if a.sealed or withheld:
            out.system_prompt = ""
            out.sealed = True
        if viewer_id is not None and a.owner_id != viewer_id:
            out.pipeline_id = None  # the owner's own binding
        return out


class AgentIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    #: The @handle, slugified server-side, unique per owner. Empty derives it from the name.
    slug: str | None = Field(default=None, max_length=60)
    description: str = ""
    model: str = ""
    #: Project-instruction sized: long enough for a full persona, bounded so it cannot
    #: eat the window.
    system_prompt: str = Field(default="", max_length=24_000)
    #: Null (or omitted) inherits every tool/skill; `[]` denies all.
    tools: list[str] | None = None
    skill_ids: list[str] | None = None
    kinds: list[str] | None = None
    #: Null: the first of `kinds` (chat when unrestricted). Must be one of `kinds` when set.
    surface: str | None = Field(default=None, max_length=20)
    #: A 서식 from `/design-templates` whose surface is one of `kinds`.
    render_template_id: str | None = Field(default=None, max_length=60)
    #: A `doc_formats` id; applies to report sessions whose 서식 fixes no format.
    doc_format: str | None = Field(default=None, max_length=40)
    design_system_id: str | None = Field(default=None, max_length=64)
    #: 담당 부서·연락처 (「학사지원팀 02-760-1234, haksa@example.ac.kr」), said word for word when
    #: the material does not answer the question.
    contact: str = Field(default="", max_length=300)
    guide: str = Field(default="", max_length=2000)
    starters: list[str] = Field(default_factory=list, max_length=6)
    share_mode: Literal["open", "sealed"] = "open"
    temperature: float = Field(default=0.7, ge=0, le=2)
    color: str = Field(default="#5b53e8", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True
    visibility: AgentVisibility = AgentVisibility.private
    #: Check skills (`app/skills`, those with a `stage`) the server runs around its writing.
    check_skills: list[str] | None = Field(default=None, max_length=12)
    #: `{tool_numbers, titles}`: how strict `grounding-check` is.
    check_options: dict[str, bool] | None = None
    #: 빠진 정보 질문 (`services/gap_questions.py`), checked on save; null keeps none stored.
    gap_questions: list[dict[str, Any]] | None = Field(default=None, max_length=12)
    #: 진행 과정 설정 (`services/process_settings.py`), checked on save; null: its 서식's.
    process_settings: dict[str, Any] | None = None


def _inherited_settings(a: Agent) -> dict:
    """What its 서식 sets (the shipped one bound to its format, or a person's template it
    names), as the form shows the defaults."""
    from app.services import process_settings

    return process_settings.resolve(template_id=a.render_template_id,
                                    format_id=a.doc_format).wire()


def _inherited_questions(a: Agent) -> list[dict]:
    """What its 서식 asks (or the 서식 bound to its format)."""
    from app.services import gap_questions

    return gap_questions.resolve(own=[], template_id=a.render_template_id,
                                 doc_format=a.doc_format)


def _base_questions(a: Agent) -> list[dict]:
    """With none of its own stored, what the official agent it runs as asks."""
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
    #: 「기본 도구」 or the connector's name; the pickers group by it.
    group: str = ""
    #: Short name a person reads; the raw `name` is technical detail.
    title: str = ""
    #: One line on what it does.
    description: str = ""


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
    #: Shown in the pickers; False for a shipped theme kept for what already wears it.
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
    #: Review checklist, one sentence per line, Korean only.
    checks: JsonList = Field(default_factory=list)
    #: Extension of the blank Office form (`docx`, `pptx`), or empty.
    form_format: str = ""
    #: Whether `/design-templates/{id}/preview` has something to show.
    has_preview: bool = False
    #: Slides in a deck's sample, each its own `/preview?slide=N`; 0 for any other kind.
    slide_count: int = 0
    #: A person's template (`ut-<id>`): the caller's own (`mine`) or one shared with them.
    user: bool = False
    mine: bool = False
    #: Shown in the pickers; False for a shipped 서식 still chosen by id but not offered.
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
    #: Administrator-only; refused otherwise.
    shared: bool = False


class KnowledgeUrl(Wire):
    """A page to read into an agent's knowledge."""

    url: str = Field(min_length=8, max_length=2000)
