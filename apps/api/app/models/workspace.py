"""Workspace models; each row single-owner, shared only via `visibility`/`shared` flags."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Index, Text, UniqueConstraint, event, inspect
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow
from app.services.search_text import artifact_text


def _uuid() -> str:
    return uuid.uuid4().hex


def _ts(**kwargs) -> Column:
    return Column(DateTime(timezone=True), **kwargs)


def _json(**kwargs) -> Column:
    return Column(JSONB, **kwargs)


class Project(SQLModel, table=True):
    """Instructions and defaults inherited by its sessions."""

    __tablename__ = "projects"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    emoji: str = Field(default="📁")
    #: prepended to every turn
    instructions: str = Field(default="")
    skill_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: null: surface defaults
    design_system_id: str | None = Field(default=None, foreign_key="design_systems.id")
    #: surface → template id; not FKs (unknown id → built-in track); null ≡ {}
    render_templates: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: index collection for project knowledge and uploads; unguessable
    index_key: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class DesignSystem(SQLModel, table=True):
    """Project-wide look: `tokens` for renderers, short `body` for the model."""

    __tablename__ = "design_systems"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    #: `{accent, ink, muted, font}`, normalised by `services.design`
    tokens: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: voice/vocabulary/don'ts; one model block
    body: str = Field(default="")
    #: English suffix for image prompts
    image_style: str = Field(default="")
    craft: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: offered to all accounts; admin-only
    shared: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class StoredFile(SQLModel, table=True):
    """Upload plus extracted text; blob on disk at `storage_key`."""

    __tablename__ = "files"
    __table_args__ = (Index("ix_files_user_project", "user_id", "project_id"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    #: project knowledge; null for chat attachment
    project_id: str | None = Field(default=None)
    session_id: str | None = Field(default=None)
    agent_id: str | None = Field(default=None)
    source_url: str | None = Field(default=None)
    #: how the file came: 「upload」 (the person gave it) or 「generated」 (made in a conversation:
    #: a code run's output, an image, a chart, an export)
    origin: str = Field(default="upload", max_length=12)
    name: str
    size: int = Field(default=0)
    mime: str = Field(default="")
    storage_key: str = Field(default="")
    text: str = Field(default="")
    tokens: int = Field(default=0)
    error: str | None = Field(default=None)
    #: last index write; null = lexical only
    indexed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: target collection, for move/delete
    index_collection: str | None = Field(default=None)
    #: OCR state: null (none needed) | queued | running | done | failed; `text` is partial
    ocr_state: str | None = Field(default=None, max_length=16)
    ocr_total: int = Field(default=0)
    ocr_done: int = Field(default=0)
    ocr_review: int = Field(default=0)
    ocr_attempts: int = Field(default=0)
    #: last claim/progress; stale → taken over
    ocr_claimed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: claim token; stale workers can't write after takeover
    ocr_claim: str | None = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class FilePage(SQLModel, table=True):
    """OCR page: text, reader, confidence; file `text` = pages joined."""

    __tablename__ = "file_pages"
    __table_args__ = (UniqueConstraint("file_id", "page", name="uq_file_pages_file_page"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    file_id: str = Field(foreign_key="files.id", ondelete="CASCADE", index=True)
    page: int
    #: "text" (PDF layer) | "engine" (ocr-shim) | "vision"
    source: str = Field(default="text", max_length=16)
    text: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    #: engine mean score; null if engine didn't read
    confidence: float | None = Field(default=None)
    #: engine/vision disagreement or partly unreadable
    needs_review: bool = Field(default=False)
    has_table: bool = Field(default=False)
    #: line boxes, render dpi/size, engine text if vision kept; for passage pointers
    layout: dict | None = Field(default=None, sa_column=_json(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class ArtifactKind(StrEnum):
    report = "report"
    deck = "deck"
    chart = "chart"
    image = "image"
    audio = "audio"
    video = "video"
    code = "code"
    html = "html"


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_user_updated", "user_id", "updated_at"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    session_id: str | None = Field(default=None)
    project_id: str | None = Field(default=None)
    kind: ArtifactKind
    title: str = Field(default="")
    version: int = Field(default=1)
    #: shape per `kind`; mirrors web union type
    data: dict | None = Field(default=None, sa_column=_json(nullable=True))
    storage_key: str | None = Field(default=None)
    #: plain text for search, synced by listeners below; never sent to a model
    search_text: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="", default="")
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


@event.listens_for(Artifact, "before_insert")
def _artifact_text_on_insert(_mapper, _connection, target: Artifact) -> None:
    target.search_text = artifact_text(target.data)


@event.listens_for(Artifact, "before_update")
def _artifact_text_on_update(_mapper, _connection, target: Artifact) -> None:
    # body changes only; title/project edits keep text
    if inspect(target).attrs.data.history.has_changes():
        target.search_text = artifact_text(target.data)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class Job(SQLModel, table=True):
    """Long-running generation (video); row written before polling to survive restarts."""

    __tablename__ = "jobs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    kind: str = Field(default="av")
    status: str = Field(default=JobStatus.queued)
    prompt: str = Field(default="")
    model: str = Field(default="")
    params: dict | None = Field(default=None, sa_column=_json(nullable=True))
    provider_job_id: str | None = Field(default=None, index=True)
    artifact_id: str | None = Field(default=None)
    credits_estimated: int = Field(default=0)
    #: charged on success only (failed clips unbilled upstream)
    credits_used: int = Field(default=0)
    progress: int = Field(default=0)
    stage: str = Field(default="")
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    finished_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))


class ArtifactVersion(SQLModel, table=True):
    """Append-only history; old body written before edit."""

    __tablename__ = "artifact_versions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    artifact_id: str = Field(foreign_key="artifacts.id", index=True)
    version: int
    data: dict | None = Field(default=None, sa_column=_json(nullable=True))
    storage_key: str | None = Field(default=None)
    summary: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class Visibility(StrEnum):
    private = "private"
    org = "org"


#: shared Postgres type `agentvisibility`
AgentVisibility = Visibility


class SkillSource(StrEnum):
    #: workspace = copied from a shared skill; personal = authored (shipped ones by the admin)
    workspace = "workspace"
    personal = "personal"


class Skill(SQLModel, table=True):
    """Per-turn procedure; `when_to_use` read by model, `enabled` = available, not injected."""

    __tablename__ = "skills"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    slug: str = Field(default="")
    description: str = Field(default="")
    when_to_use: str = Field(default="")
    body: str = Field(default="")
    #: shipped-skill identity; null if user-authored
    catalog_key: str | None = Field(default=None)
    #: owner row: content hash at the last package sync; differs once the owner edits it
    package_hash: str | None = Field(default=None, max_length=32)
    source: SkillSource = Field(default=SkillSource.personal)
    #: shared = copyable; runs only from owner's account
    visibility: Visibility = Field(
        default=Visibility.private,
        sa_column=Column(
            PgEnum(Visibility, name="agentvisibility", create_type=False),
            nullable=False,
            server_default=Visibility.private.value,
        ),
    )
    installs: int = Field(default=0)
    #: copy source; reinstall no-op
    origin_id: str | None = Field(default=None, index=True)
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: tools that must survive agent allowlist
    required_tools: list | None = Field(default=None, sa_column=_json(nullable=True))
    estimated_tokens: int = Field(default=0)
    version: str = Field(default="1.0.0")
    enabled: bool = Field(default=True)
    #: server-run fields naming shipped scripts only (`services/skill_specs.py`); null: none stored
    server_fields: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: user scripts; sandbox-only via `run_skill_script`, never imported
    script_files: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: admin review for sharing scripted skills; script edit → back to private
    review: str = Field(default="", max_length=20)
    review_note: str = Field(default="")
    reviewed_by: str | None = Field(default=None)
    reviewed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class MemoryType(StrEnum):
    user = "user"
    feedback = "feedback"
    project = "project"
    reference = "reference"


class Memory(SQLModel, table=True):
    __tablename__ = "memories"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    type: MemoryType = Field(default=MemoryType.user)
    body: str = Field(default="")
    scope: str = Field(default="global")
    links: list | None = Field(default=None, sa_column=_json(nullable=True))
    pinned: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class Agent(SQLModel, table=True):
    __tablename__ = "agents"
    #: one @handle per owner; backstop for `_claim_slug`
    __table_args__ = (UniqueConstraint("owner_id", "slug", name="ux_agents_owner_slug"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    slug: str = Field(default="")
    description: str = Field(default="")
    model: str = Field(default="")
    system_prompt: str = Field(default="")
    #: null inherits; [] denies all; list = hard allowlist
    tools: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: same three-state contract as tools
    skill_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: skills on in every turn of its conversations (an agent built from skills); null: none
    active_skill_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: initial surface (one of `kinds`); others secondary
    surface: str | None = Field(default=None)
    #: 서식 on its surface; beats project default
    render_template_id: str | None = Field(default=None)
    #: `doc_formats` id when 서식 fixes none
    doc_format: str | None = Field(default=None)
    #: beats project's; not a FK (same-name user design used instead)
    design_system_id: str | None = Field(default=None)
    #: user pipeline replacing shipped flow
    pipeline_id: str | None = Field(default=None)
    #: official agent whose process this one runs (`services/bases.py`); null: plain/own flow
    base_key: str | None = Field(default=None, max_length=60)
    #: server-run check skills (`app/skills/README.md`); null: none
    check_skills: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: `grounding-check` strictness: `tool_numbers`, `titles`
    check_options: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: ticked 묶음 (`services/bundles.py`), compiled into checks/pipeline; null: pre-bundle
    bundles: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: 빠진 정보 질문 beyond the 서식's; null: base agent's
    gap_questions: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: own 진행 과정 설정 over the 서식's; null: 서식's
    process_settings: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: out of catalogue; row kept so old work opens/exports
    retired: bool = Field(default=False)
    #: `open`: copy carries prompt; `sealed`: copy runs original's prompt unseen
    share_mode: str = Field(default="open")
    #: sealed copy; prompt read from `origin_id` at run time
    sealed: bool = Field(default=False)
    #: 담당 부서·연락처, said verbatim; empty: 「담당 부서에 문의해 주세요」
    contact: str = Field(default="")
    guide: str = Field(default="")
    #: empty-screen starter buttons, sent verbatim
    starters: list | None = Field(default=None, sa_column=_json(nullable=True))
    temperature: float = Field(default=0.7)
    #: language every turn and document is written in: `auto` (the request's), `ko`, `en`,
    #: `ja`, `zh` or a language named in free text; a turn naming another language wins
    output_language: str = Field(default="auto", max_length=40)
    #: web search a new conversation starts with: `auto`, `on`, `off`; toggled per turn
    web_search_default: str = Field(default="auto", max_length=10)
    #: account memories read into its turns and written from them
    use_memory: bool = Field(default=True)
    color: str = Field(default="#5b53e8")
    enabled: bool = Field(default=True)
    #: explicit type name, shared with `Skill.visibility`
    visibility: Visibility = Field(
        default=Visibility.private,
        sa_column=Column(
            PgEnum(Visibility, name="agentvisibility", create_type=False),
            nullable=False,
            server_default=Visibility.private.value,
        ),
    )
    #: index collection; never derived from `id` (name is the only authorisation)
    index_key: str | None = Field(default=None)
    installs: int = Field(default=0)
    #: catalogue identity; null if user-authored
    catalog_key: str | None = Field(default=None)
    #: copy source; same contract as `Skill.origin_id`
    origin_id: str | None = Field(default=None, index=True)
    #: frozen publication copy of this agent id; bot-only
    snapshot_of: str | None = Field(default=None, index=True)
    runs: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class ShareScope(StrEnum):
    workspace = "workspace"
    link = "link"


class Share(SQLModel, table=True):
    """Capability URL for one artifact/conversation; response exposes only the shared thing."""

    __tablename__ = "shares"

    id: str = Field(default_factory=_uuid, primary_key=True)
    token: str = Field(index=True, unique=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    artifact_id: str | None = Field(default=None, foreign_key="artifacts.id", index=True)
    session_id: str | None = Field(default=None, foreign_key="sessions.id")
    scope: ShareScope = Field(default=ShareScope.link)
    views: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))


class ShareView(SQLModel, table=True):
    """Share open log, one row per reader per hour; name/email copied, not joined."""

    __tablename__ = "share_views"

    id: str = Field(default_factory=_uuid, primary_key=True)
    share_id: str = Field(foreign_key="shares.id", index=True)
    #: visit start; `last_at` advances
    at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    last_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    opens: int = Field(default=1)
    #: null = anonymous link reader
    viewer_id: str | None = Field(default=None, foreign_key="users.id", index=True)
    viewer_name: str = Field(default="")
    viewer_email: str = Field(default="")
    #: first `X-Forwarded-For` hop
    ip: str = Field(default="")
    #: raw, shortened only for display
    user_agent: str = Field(default="")


class Transport(StrEnum):
    stdio = "stdio"
    http = "http"
    sse = "sse"


class ConnectorStatus(StrEnum):
    connected = "connected"
    disconnected = "disconnected"
    needs_auth = "needs_auth"
    error = "error"


class Connector(SQLModel, table=True):
    """User-installed MCP server; secrets in `ConnectorCredential`."""

    __tablename__ = "connectors"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    slug: str = Field(default="")
    description: str = Field(default="")
    category: str = Field(default="")
    transport: Transport = Field(default=Transport.stdio)
    #: stdio command or http/sse URL
    endpoint: str = Field(default="")
    #: stdio env; `{{USER_ID}}` etc. substituted per caller
    env: dict | None = Field(default=None, sa_column=_json(nullable=True))
    auth_type: str = Field(default="none")
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    official: bool = Field(default=False)
    installed: bool = Field(default=True)
    enabled: bool = Field(default=True)
    status: ConnectorStatus = Field(default=ConnectorStatus.disconnected)
    last_sync_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class ConnectorTool(SQLModel, table=True):
    """Connector tool with own toggle."""

    __tablename__ = "connector_tools"
    __table_args__ = (Index("ix_connector_tools_connector", "connector_id", "name"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    connector_id: str = Field(foreign_key="connectors.id", index=True)
    name: str
    description: str = Field(default="")
    parameters: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: write tools off until opted in
    read_only: bool = Field(default=True)
    enabled: bool = Field(default=True)


class ConnectorCredential(SQLModel, table=True):
    """Connector secrets; never serialised to the browser."""

    __tablename__ = "connector_credentials"

    connector_id: str = Field(foreign_key="connectors.id", primary_key=True)
    payload: dict | None = Field(default=None, sa_column=_json(nullable=True))
    expires_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class Template(SQLModel, table=True):
    """Retired 시작점; unread, rows kept."""

    __tablename__ = "templates"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    kind: str = Field(default="report")
    #: gallery chip; free text
    group: str = Field(default="내 템플릿")
    title: str
    description: str = Field(default="")
    fills: list | None = Field(default=None, sa_column=_json(nullable=True))
    examples: list | None = Field(default=None, sa_column=_json(nullable=True))
    needs: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: ends mid-sentence for the person
    prompt: str = Field(default="")
    #: form file; text reaches the model
    file_id: str | None = Field(default=None, foreign_key="files.id", index=True)
    #: not a FK (retired ids stay readable); empty: no fixed shape
    render_template_id: str = Field(default="", max_length=60)
    #: offered to all accounts; admin-only
    shared: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class UserTemplate(SQLModel, table=True):
    """User-made 템플릿 with all shipped 서식 fields; shared `_document` seed, id `ut-<id>`."""

    __tablename__ = "user_templates"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str = Field(max_length=60)
    description: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=20)
    fills: list | None = Field(default=None, sa_column=_json(nullable=True))
    example_prompt: str = Field(default="")
    instructions: str = Field(default="")
    checklist: str = Field(default="")
    genre: str = Field(default="")
    #: `{label, fields, numbering, head, lists, closing, subtitle}`
    doc_format: dict | None = Field(default=None, sa_column=_json(nullable=True))
    cover_page: bool = Field(default=False)
    #: `design`: body face follows design font token
    body_face: str = Field(default="", max_length=10)
    check_skills: list | None = Field(default=None, sa_column=_json(nullable=True))
    check_options: list | None = Field(default=None, sa_column=_json(nullable=True))
    questions: list | None = Field(default=None, sa_column=_json(nullable=True))
    process: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: source form; reference text only, never export base
    form_name: str = Field(default="", max_length=200)
    form_text: str = Field(default="")
    #: `private` or `org` (owner-only edits)
    visibility: str = Field(default="private", max_length=10)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class ConnectorCatalogEntry(SQLModel, table=True):
    """Admin-kept installable MCP server; stdio admin-only, `env` encrypted and never returned."""

    __tablename__ = "connector_catalog"

    id: str = Field(default_factory=_uuid, primary_key=True)
    slug: str = Field(max_length=60, unique=True, index=True)
    name: str = Field(max_length=80)
    description: str = Field(default="", max_length=300)
    category: str = Field(default="", max_length=30)
    transport: str = Field(default="http", max_length=10)
    endpoint: str = Field(default="", max_length=500)
    env: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: `[{key, label, hint, secret}]` collected at install
    required_env: list | None = Field(default=None, sa_column=_json(nullable=True))
    auth: str = Field(default="none", max_length=20)
    tools_key_var: str = Field(default="", max_length=60)
    tools_key_feature: str = Field(default="", max_length=20)
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    enabled: bool = Field(default=True)
    #: members' catalogue; unlisted still installable by slug
    listed: bool = Field(default=True)
    #: 기본 제공: installed for every active person, who can switch it off but not remove it
    by_default: bool = Field(default=False)
    position: int = Field(default=0)
    updated_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class UserPipeline(SQLModel, table=True):
    """User-composed pipeline; private until admin-approved for org, edit → back to private."""

    __tablename__ = "user_pipelines"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    base: str | None = Field(default=None)
    origin_id: str | None = Field(default=None, index=True)
    #: owning agent's 진행 과정 (`@self` steps); null: standalone 워크플로
    agent_id: str | None = Field(default=None, index=True)
    #: "" pipeline | `bundle` (묶음)
    kind: str = Field(default="", max_length=20)
    stages: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: `[{from, to, branch?}]` by step id; null: steps run in order
    edges: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: org only via approval
    visibility: str = Field(default="private")
    #: "" · pending · approved · rejected
    review: str = Field(default="")
    review_note: str = Field(default="")
    reviewed_by: str | None = Field(default=None)
    reviewed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: bumped per edit; runs pin their version
    version: int = Field(default=1)
    installs: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


__all__ = [
    "Job",
    "JobStatus",
    "Agent",
    "AgentVisibility",
    "Visibility",
    "Artifact",
    "ArtifactKind",
    "ArtifactVersion",
    "Connector",
    "ConnectorCredential",
    "ConnectorStatus",
    "ConnectorTool",
    "DesignSystem",
    "Memory",
    "MemoryType",
    "Project",
    "Skill",
    "SkillSource",
    "StoredFile",
    "Template",
    "Transport",
    "UserPipeline",
]
