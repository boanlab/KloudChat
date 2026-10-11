"""Workspace: projects, files, artifacts, skills, memories, agents, connectors.

Every row is owned by exactly one user; `visibility`/`shared` flags are the only sharing.
"""

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


# ── projects ───────────────────────────────────────────────────────────


class Project(SQLModel, table=True):
    """A project: instructions and defaults every session started inside it inherits."""

    __tablename__ = "projects"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    emoji: str = Field(default="📁")
    #: Prepended to every turn in this project.
    instructions: str = Field(default="")
    skill_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Null: surface defaults (model-picked accent, exporter fonts).
    design_system_id: str | None = Field(default=None, foreign_key="design_systems.id")
    #: Surface → rendering template id. Not foreign keys: the catalogue ships in
    #: the image, and an unknown id degrades to the built-in track. Null and
    #: `{}` are equivalent.
    render_templates: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: Retrieval-index collection for the project's knowledge and the uploads made in
    #: its conversations, minted on the first indexed document. Unguessable on purpose.
    index_key: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class DesignSystem(SQLModel, table=True):
    """One look shared by every surface a project produces.

    `tokens` is read by the renderers (every exporter and the preview must be
    able to express each token); `body` is read by the model and kept short.
    """

    __tablename__ = "design_systems"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    #: `{accent, ink, muted, font}`. Normalised on write by `services.design`.
    tokens: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: Voice, vocabulary, things not to write; reaches the model as one block.
    body: str = Field(default="")
    #: English phrase appended to every image prompt (image prompts are English).
    image_style: str = Field(default="")
    #: Keys of `design.CRAFT`.
    craft: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Offered to every account. Administrator-only.
    shared: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── files ──────────────────────────────────────────────────────────────


class StoredFile(SQLModel, table=True):
    """An upload plus its extracted text. Text lives in the row; the blob is on
    disk under `storage_key`."""

    __tablename__ = "files"
    __table_args__ = (Index("ix_files_user_project", "user_id", "project_id"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    #: Set when the file is project knowledge; null for a one-off chat attachment.
    project_id: str | None = Field(default=None)
    session_id: str | None = Field(default=None)
    #: Set when the file is an agent's own knowledge, searchable by that agent.
    agent_id: str | None = Field(default=None)
    #: Source page when ingested from the web rather than uploaded.
    source_url: str | None = Field(default=None)
    name: str
    size: int = Field(default=0)
    mime: str = Field(default="")
    storage_key: str = Field(default="")
    text: str = Field(default="")
    #: Estimate: len(text) / 3.5.
    tokens: int = Field(default=0)
    #: Set when extraction failed, so the UI can say why instead of showing 0 chars.
    error: str | None = Field(default=None)
    #: Last successful write to the retrieval index; null means lexical search only.
    indexed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: The collection that write went to, so a move or a delete forgets the right one.
    index_collection: str | None = Field(default=None)
    #: Reading scanned pages (`services/ocr_jobs.py`): null when the file needed none,
    #: else "queued" | "running" | "done" | "failed". `text` holds what is read so far.
    ocr_state: str | None = Field(default=None, max_length=16)
    #: Pages to read, pages read, and pages whose two readings disagreed.
    ocr_total: int = Field(default=0)
    ocr_done: int = Field(default=0)
    ocr_review: int = Field(default=0)
    ocr_attempts: int = Field(default=0)
    #: When a worker last claimed or advanced the job; a stale one is taken over.
    ocr_claimed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class FilePage(SQLModel, table=True):
    """One page of a file that went through OCR: what was read, by which reader, and how
    sure it is. The file's `text` is these pages joined in order."""

    __tablename__ = "file_pages"
    __table_args__ = (UniqueConstraint("file_id", "page", name="uq_file_pages_file_page"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    file_id: str = Field(foreign_key="files.id", ondelete="CASCADE", index=True)
    #: 1-based page (or TIFF frame).
    page: int
    #: "text" (the PDF's own text layer), "engine" (ocr-shim) or "vision" (a vision model).
    source: str = Field(default="text", max_length=16)
    text: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    #: The engine's mean recognition score; null when the engine did not read the page.
    confidence: float | None = Field(default=None)
    #: The engine and the vision model disagreed (words or numbers), or the reader said
    #: part of the page could not be read.
    needs_review: bool = Field(default=False)
    has_table: bool = Field(default=False)
    #: Lines with boxes in page pixels, the render dpi and size, and the engine's own
    #: text when the vision model's was kept. For pointing at a passage later.
    layout: dict | None = Field(default=None, sa_column=_json(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── artifacts ──────────────────────────────────────────────────────────


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
    #: Shape depends on `kind`; mirrors the discriminated union in the web client's types.
    data: dict | None = Field(default=None, sa_column=_json(nullable=True))
    storage_key: str | None = Field(default=None)
    #: What the result says as plain words, for search (`services.search_text`). Kept in step
    #: with `data` on every ORM write by the listeners below; never sent to a model.
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
    # Only when the body was assigned: a title or project change leaves the text as it was.
    if inspect(target).attrs.data.history.has_changes():
        target.search_text = artifact_text(target.data)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class Job(SQLModel, table=True):
    """Long-running generation (video) that outlives its request.

    `provider_job_id` is the upstream handle; the row is written before polling
    starts so a clip survives a restart.
    """

    __tablename__ = "jobs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    kind: str = Field(default="av")
    #: Text column holding a `JobStatus` value.
    status: str = Field(default=JobStatus.queued)
    prompt: str = Field(default="")
    model: str = Field(default="")
    #: Resolution, duration, audio: what the price was quoted from.
    params: dict | None = Field(default=None, sa_column=_json(nullable=True))
    provider_job_id: str | None = Field(default=None, index=True)
    artifact_id: str | None = Field(default=None)
    #: Quoted before the run, from the model's price sheet.
    credits_estimated: int = Field(default=0)
    #: Charged on success only; the upstream does not bill for a failed clip.
    credits_used: int = Field(default=0)
    progress: int = Field(default=0)
    stage: str = Field(default="")
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    finished_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))


class ArtifactVersion(SQLModel, table=True):
    """Append-only history. Editing an artifact writes the old body here first."""

    __tablename__ = "artifact_versions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    artifact_id: str = Field(foreign_key="artifacts.id", index=True)
    version: int
    data: dict | None = Field(default=None, sa_column=_json(nullable=True))
    storage_key: str | None = Field(default=None)
    summary: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── skills ─────────────────────────────────────────────────────────────


class Visibility(StrEnum):
    private = "private"
    org = "org"


#: Skills and agents share the Postgres type `agentvisibility`.
AgentVisibility = Visibility


class SkillSource(StrEnum):
    built_in = "built-in"
    workspace = "workspace"
    personal = "personal"


class Skill(SQLModel, table=True):
    """An installed procedure that may be activated for one turn.

    `when_to_use` is what the model reads to decide whether the skill applies.
    `enabled` means available, not "inject into every prompt".
    """

    __tablename__ = "skills"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    slug: str = Field(default="")
    description: str = Field(default="")
    when_to_use: str = Field(default="")
    #: The body of SKILL.md.
    body: str = Field(default="")
    #: Stable identity for a shipped skill. Null for user-authored skills.
    catalog_key: str | None = Field(default=None)
    source: SkillSource = Field(default=SkillSource.personal)
    #: Sharing means "copyable" from the store; a skill only runs from its owner's account.
    visibility: Visibility = Field(
        default=Visibility.private,
        sa_column=Column(
            PgEnum(Visibility, name="agentvisibility", create_type=False),
            nullable=False,
            server_default=Visibility.private.value,
        ),
    )
    #: Copies taken; written by the install route only.
    installs: int = Field(default=0)
    #: Shared row this one was copied from; a second install is a no-op.
    origin_id: str | None = Field(default=None, index=True)
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Tool names that must survive the agent's hard allowlist for this skill.
    required_tools: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Approximate prompt cost, shown before activation.
    estimated_tokens: int = Field(default=0)
    version: str = Field(default="1.0.0")
    enabled: bool = Field(default=True)
    #: What the server runs for it (`services/skill_specs.py`): `stage`, `entry`, `post_check`,
    #: `post_judge`, `copy_check`, `copy_kinds`, `tables`, `own_stage`, `uses` — each naming
    #: shipped scripts only. Null: none stored (a shipped copy then reads its package).
    server_fields: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: The person's own scripts, `[{name, source, summary, size}]`: run only in the code
    #: sandbox through `run_skill_script`, never imported by the server.
    script_files: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Sharing a skill with scripts waits for an administrator, as a 직접 구성 does:
    #: "" · pending · approved · rejected (with `review_note`). An edit to its scripts sends
    #: an approved skill back to private.
    review: str = Field(default="", max_length=20)
    review_note: str = Field(default="")
    reviewed_by: str | None = Field(default=None)
    reviewed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── memories ───────────────────────────────────────────────────────────


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
    #: 'global' or a project id. Scoped memories only load inside that project.
    scope: str = Field(default="global")
    links: list | None = Field(default=None, sa_column=_json(nullable=True))
    pinned: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── agents ─────────────────────────────────────────────────────────────


class Agent(SQLModel, table=True):
    __tablename__ = "agents"
    #: One @handle per owner; backstop behind `_claim_slug` in routers/workspace.py.
    __table_args__ = (UniqueConstraint("owner_id", "slug", name="ux_agents_owner_slug"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    slug: str = Field(default="")
    description: str = Field(default="")
    model: str = Field(default="")
    system_prompt: str = Field(default="")
    #: Tool names this agent may call. Null inherits the user's available tools;
    #: an empty list denies every tool; a populated list is a hard allowlist.
    tools: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Same three-state contract as tools: null inherits turn selection, []
    #: denies skills, and values form a hard allowlist.
    skill_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Surface a new conversation with this agent opens on; one of `kinds` when that is set.
    #: The other `kinds` stay reachable as secondary outputs (handoff, start buttons).
    surface: str | None = Field(default=None)
    #: 서식 for sessions on that 서식's surface; wins over the project's default 서식.
    render_template_id: str | None = Field(default=None)
    #: `doc_formats` id for report sessions whose 서식 fixes none.
    doc_format: str | None = Field(default=None)
    #: Design system for the agent's sessions; wins over the project's. Not a foreign key:
    #: a shared or seeded agent may name its owner's design, and the user's own design of
    #: the same name is worn instead (none when they hold none).
    design_system_id: str | None = Field(default=None)
    #: A user pipeline (`UserPipeline`) the agent's turns run instead of its shipped flow.
    pipeline_id: str | None = Field(default=None)
    #: The official agent (catalogue key) whose process a person's own agent runs: its checks,
    #: its question card and its pipeline (`services/bases.py`). Null: plain chat, or the
    #: shipped flow of `catalog_key` / the original's for a catalogue agent and its copies.
    base_key: str | None = Field(default=None, max_length=60)
    #: Check skills the server runs around this agent's writing (`app/skills/README.md`):
    #: `gap-check` asks before writing, `grounding-check` and `generic-language-killer` fix
    #: after. A catalogue agent's come from its toml `checks`; a person's own are ticked in
    #: the form (추가로 할 일). Null: none.
    check_skills: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: How strict `grounding-check` is: `tool_numbers` (a number counts only when a tool
    #: printed it), `titles` (job titles and product names are traced too).
    check_options: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: The 묶음 ticked in the form, `[{"bundle": ref, "items": {id: tick}}]`
    #: (`services/bundles.py`); `check_skills`, `check_options` and the agent's own pipeline
    #: are compiled from them. Null: none stored (an agent from before bundles).
    bundles: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: 빠진 정보 질문 (`services/gap_questions.py`): what it asks before writing when the
    #: material does not answer it, on top of its 서식's. Null: none stored (a copy or an agent
    #: on a base asks what the official agent asks).
    gap_questions: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: 진행 과정 설정 of its own (`services/process_settings.py`): calculations, 서식 보정
    #: stages, checks, 검토 규칙, structure and options over its 서식's. Null: its 서식's.
    process_settings: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: Taken out of the catalogue: listed nowhere and no new conversation starts with it. The
    #: row stays so conversations and documents made with it still open and export.
    retired: bool = Field(default=False)
    #: `open`: a copy carries the prompt. `sealed`: a copy runs on the
    #: original's prompt without holding it.
    share_mode: str = Field(default="open")
    #: Copy of a sealed original; its prompt is read from `origin_id` at run time.
    sealed: bool = Field(default=False)
    #: 담당 부서·연락처: who to ask when the material does not answer, said verbatim by the
    #: agent (and by the 「담당 부서 안내」 check). Empty: 「담당 부서에 문의해 주세요」.
    contact: str = Field(default="")
    #: Usage notes shown on the empty conversation screen.
    guide: str = Field(default="")
    #: First messages offered as buttons on the empty screen, sent verbatim.
    starters: list | None = Field(default=None, sa_column=_json(nullable=True))
    temperature: float = Field(default=0.7)
    color: str = Field(default="#5b53e8")
    enabled: bool = Field(default=True)
    #: Postgres type name is explicit because `Skill.visibility` shares it.
    visibility: Visibility = Field(
        default=Visibility.private,
        sa_column=Column(
            PgEnum(Visibility, name="agentvisibility", create_type=False),
            nullable=False,
            server_default=Visibility.private.value,
        ),
    )
    #: Collection name in the retrieval index; minted on first use, never derived
    #: from `id` (the name is the index's only authorisation).
    index_key: str | None = Field(default=None)
    installs: int = Field(default=0)
    #: Stable identity for a catalogue agent; null for user-authored ones.
    catalog_key: str | None = Field(default=None)
    #: Shared agent this one was copied from; same contract as `Skill.origin_id`.
    origin_id: str | None = Field(default=None, index=True)
    #: A published version's fixed copy (`publication_versions`) of the agent named here:
    #: listed nowhere, edited by no one, run only by the bot.
    snapshot_of: str | None = Field(default=None, index=True)
    runs: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class ShareScope(StrEnum):
    #: Signed in, any member of this instance.
    workspace = "workspace"
    #: Whoever holds the URL. No account, no session.
    link = "link"


class Share(SQLModel, table=True):
    """A capability URL for one artifact or one conversation.

    The token is the whole permission on the public route, so the response
    carries only the shared thing: no owner, project or neighbouring artifacts.
    """

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
    """Who opened a shared link, and when. One row per reader per hour.

    Name and email are copied, not joined, so the log does not rewrite itself
    when an account is renamed or deleted.
    """

    __tablename__ = "share_views"

    id: str = Field(default_factory=_uuid, primary_key=True)
    share_id: str = Field(foreign_key="shares.id", index=True)
    #: First open of this visit; `last_at` moves as it continues.
    at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    last_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    opens: int = Field(default=1)
    #: Set when the reader was signed in. Null is an anonymous `link` reader.
    viewer_id: str | None = Field(default=None, foreign_key="users.id", index=True)
    viewer_name: str = Field(default="")
    viewer_email: str = Field(default="")
    #: First hop of `X-Forwarded-For`; empty rather than a proxy's own address.
    ip: str = Field(default="")
    #: Raw `User-Agent`, stored whole and shortened only for display.
    user_agent: str = Field(default="")


# ── connectors (MCP) ───────────────────────────────────────────────────


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
    """An MCP server this user has installed. Credentials live in `ConnectorCredential`."""

    __tablename__ = "connectors"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    slug: str = Field(default="")
    description: str = Field(default="")
    category: str = Field(default="")
    transport: Transport = Field(default=Transport.stdio)
    #: A command line for stdio, a URL for http/sse.
    endpoint: str = Field(default="")
    #: Extra process env for stdio servers; `{{USER_ID}}` etc. substituted per
    #: caller at spawn (services/mcp.py).
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
    """One tool a connector exposes, with its own on/off."""

    __tablename__ = "connector_tools"
    __table_args__ = (Index("ix_connector_tools_connector", "connector_id", "name"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    connector_id: str = Field(foreign_key="connectors.id", index=True)
    name: str
    description: str = Field(default="")
    parameters: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: Write tools default to off and require an explicit opt-in.
    read_only: bool = Field(default=True)
    enabled: bool = Field(default=True)


class ConnectorCredential(SQLModel, table=True):
    """Secrets for a connector. Never serialised to the browser."""

    __tablename__ = "connector_credentials"

    connector_id: str = Field(foreign_key="connectors.id", primary_key=True)
    payload: dict | None = Field(default=None, sa_column=_json(nullable=True))
    expires_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class Template(SQLModel, table=True):
    """Legacy: a user-authored 시작점 (starting point), optionally carrying a form file.

    The feature is retired; nothing reads or writes this table. The model stays so
    the schema and existing rows are kept until a migration decides their fate.
    """

    __tablename__ = "templates"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    #: Surface it starts: report, slides, chat, image, av.
    kind: str = Field(default="report")
    #: Gallery filter chip. Free text; the built-ins use 학업/업무/연구.
    group: str = Field(default="내 템플릿")
    title: str
    description: str = Field(default="")
    #: Blanks the person has to fill, shown as chips.
    fills: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: One worked example per blank, in `fills` order.
    examples: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Requirements the job cannot run without: `web`, `file`.
    needs: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Ends mid-sentence, where the person takes over.
    prompt: str = Field(default="")
    #: Uploaded form this template writes into; its extracted text reaches the model.
    file_id: str | None = Field(default=None, foreign_key="files.id", index=True)
    #: Rendering template id. Not a foreign key: the catalogue ships in the
    #: image, and a retired id must leave the row readable. Empty: no fixed shape.
    render_template_id: str = Field(default="", max_length=60)
    #: Offered to every account. Administrator-only.
    shared: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── a person's templates ───────────────────────────────────────────────


class UserTemplate(SQLModel, table=True):
    """A document 템플릿 a person made in the editor (`services/user_templates.py`).

    It holds every field a shipped 서식 sets (`app/design_templates/<id>/template.toml` and its
    files): what to bring, the instructions and checklist, the genre rule, the document format
    (head fields, numbering, head layout, 개조식 lists, 「끝.」), the 빠진 정보 질문, the 진행
    과정 설정 (calculations, checks, 검토 규칙, structure) and the check skills. It is drawn in the
    shared document seed (`_document`) with the design's tokens: no HTML or CSS of its own.
    A form file it was made from is kept as reference text only. Sessions and agents name it
    `ut-<id>`."""

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
    #: The document format: `{label, fields, numbering, head, lists, closing, subtitle}`.
    doc_format: dict | None = Field(default=None, sa_column=_json(nullable=True))
    cover_page: bool = Field(default=False)
    #: `design`: the body face follows the design's font token, as 개조식 보고서 does.
    body_face: str = Field(default="", max_length=10)
    check_skills: list | None = Field(default=None, sa_column=_json(nullable=True))
    check_options: list | None = Field(default=None, sa_column=_json(nullable=True))
    questions: list | None = Field(default=None, sa_column=_json(nullable=True))
    process: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: The form it was made from: its name and extracted text, given to the writer as
    #: reference only (never the export base).
    form_name: str = Field(default="", max_length=200)
    form_text: str = Field(default="")
    #: `private` or `org` (others pick it; only the owner edits it).
    visibility: str = Field(default="private", max_length=10)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── the connector catalogue ────────────────────────────────────────────


class ConnectorCatalogEntry(SQLModel, table=True):
    """An installable MCP server, kept by administrators in 관리 › 커넥터 카탈로그
    (`services/tools/catalog.py`). Members install from it; a stdio entry is a command only an
    administrator may put here. `env` values are stored encrypted and never sent back; the
    tools gateway key goes in only when `tools_key_var` resolves to the address of the tool
    `tools_key_feature` names."""

    __tablename__ = "connector_catalog"

    id: str = Field(default_factory=_uuid, primary_key=True)
    slug: str = Field(max_length=60, unique=True, index=True)
    name: str = Field(max_length=80)
    description: str = Field(default="", max_length=300)
    category: str = Field(default="", max_length=30)
    transport: str = Field(default="http", max_length=10)
    endpoint: str = Field(default="", max_length=500)
    env: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: Credentials the install dialog collects: `[{key, label, hint, secret}]`.
    required_env: list | None = Field(default=None, sa_column=_json(nullable=True))
    auth: str = Field(default="none", max_length=20)
    tools_key_var: str = Field(default="", max_length=60)
    tools_key_feature: str = Field(default="", max_length=20)
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    enabled: bool = Field(default=True)
    #: Shown in the members' catalogue. An unlisted entry is still installable by slug and
    #: what is installed from it keeps working; administrators see it marked (migration 0063).
    listed: bool = Field(default=True)
    position: int = Field(default=0)
    updated_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── user pipelines ─────────────────────────────────────────────────────


class UserPipeline(SQLModel, table=True):
    """A pipeline a person composed from agents, skills and checks (`app.pipeline.compose`).

    Private to its owner until an administrator approves it for the organisation: `review`
    goes "" → `pending` (submitted) → `approved` (shown in the store, `visibility` org) or
    `rejected` (with `review_note`). An edit sends an approved pipeline back to private."""

    __tablename__ = "user_pipelines"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    #: The shipped pipeline (`app/pipelines/*.toml`) or the shared one it was copied from.
    base: str | None = Field(default=None)
    origin_id: str | None = Field(default=None, index=True)
    #: The agent whose own 진행 과정 this is (`PUT /agents/{id}/process`); its `@self` steps run
    #: that agent. Null for a pipeline composed on its own (the earlier 워크플로).
    agent_id: str | None = Field(default=None, index=True)
    #: "" for a pipeline; `bundle` for a person's own 묶음 (`services/bundles.py`), whose
    #: stages are the items an agent form ticks.
    kind: str = Field(default="", max_length=20)
    #: `[{id, kind, ref, settings}]`, validated by `compose.validate` on every write.
    stages: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: `private` or `org`; org only through approval.
    visibility: str = Field(default="private")
    #: "" · pending · approved · rejected
    review: str = Field(default="")
    review_note: str = Field(default="")
    reviewed_by: str | None = Field(default=None)
    reviewed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    #: Counts every saved edit; a run keeps the version it ran.
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
