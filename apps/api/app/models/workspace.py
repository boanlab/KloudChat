"""Workspace: projects, files, artifacts, skills, memories, agents, connectors.

Everything here is owned by exactly one user. The only sharing is an agent's
`visibility` flag; ownership is a plain column, so a real sharing model would
be an ACL table rather than a rework of these rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


def _uuid() -> str:
    return uuid.uuid4().hex


def _ts(**kwargs) -> Column:
    return Column(DateTime(timezone=True), **kwargs)


def _json(**kwargs) -> Column:
    return Column(JSONB, **kwargs)


# ── projects ───────────────────────────────────────────────────────────


class Project(SQLModel, table=True):
    """The work, and the defaults everything started inside it begins with.

    `render_templates` maps surface → template id rather than a column per
    surface: only two of five surfaces have a rendering track, and the only reader
    holds the kind of the session being created, which a map answers with a lookup.

    The values are ids in the shipped catalogue and deliberately not foreign keys,
    as `sessions.render_template_id` is not — the catalogue lives in the image, so
    an id an upgrade removes degrades to "no format".
    """

    __tablename__ = "projects"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    emoji: str = Field(default="📁")
    #: Prepended to every turn in this project. The whole point of a project.
    instructions: str = Field(default="")
    skill_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: The look everything this project produces wears. Null means the surface
    #: defaults stand — the model picks the deck accent and the exporters use
    #: their own fonts, exactly as before design systems existed.
    design_system_id: str | None = Field(default=None, foreign_key="design_systems.id")
    #: Surface → rendering template: the format a new session on that surface
    #: starts in. Null and `{}` both mean the built-in track, which is what a
    #: project that never chose a format has always produced.
    render_templates: dict | None = Field(default=None, sa_column=_json(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class DesignSystem(SQLModel, table=True):
    """One look, shared by every surface a project produces.

    Split in two on purpose.

    `tokens` is what the **renderers** read — four values that all three
    exporters (`.pptx`, `.pdf`, `.hwpx`) and the browser preview can each
    express. Anything a renderer cannot draw does not belong here: a token that
    only survives to PowerPoint is a preview that lies.

    `body` is what the **model** reads, and it is capped short. A design system
    is the rule several projects share; anything longer than a few lines is that
    one project's instructions, which already have a field of their own.

    `image_style` is separate from `body` because it leaves in a different
    language — image prompts are composed in English phrases alongside
    `imagegen._STYLE_PHRASE`, and Korean prose dropped into one is noise.
    """

    __tablename__ = "design_systems"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    #: `{accent, ink, muted, font}`. Normalised on write by `services.design`.
    tokens: dict | None = Field(default=None, sa_column=_json(nullable=True))
    #: Voice, vocabulary, things not to write. Reaches the model as one block.
    body: str = Field(default="")
    #: English phrase appended to every image prompt in this project.
    image_style: str = Field(default="")
    #: Which brand-agnostic craft rules to carry — keys of `design.CRAFT`.
    craft: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Offered to every account. Administrator-only, like `Template.shared`.
    shared: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


# ── files ──────────────────────────────────────────────────────────────


class StoredFile(SQLModel, table=True):
    """An upload plus its extracted text.

    Text lives in the row: prompt assembly reads it by id, and a round trip to
    disk per file would be latency on the critical path. Blobs stay on disk
    under `storage_key` for download and re-extraction.
    """

    __tablename__ = "files"
    __table_args__ = (Index("ix_files_user_project", "user_id", "project_id"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    #: Set when the file is project knowledge; null for a one-off chat attachment.
    project_id: str | None = Field(default=None)
    session_id: str | None = Field(default=None)
    #: Set when the file is an agent's own knowledge, searchable by that agent.
    agent_id: str | None = Field(default=None)
    #: Where the text came from, when it was ingested from a page rather than
    #: uploaded. A snapshot: the page may have changed since.
    source_url: str | None = Field(default=None)
    name: str
    size: int = Field(default=0)
    mime: str = Field(default="")
    storage_key: str = Field(default="")
    text: str = Field(default="")
    #: Rough — length/3.5. Enough to warn before a prompt blows the window.
    tokens: int = Field(default=0)
    #: Set when extraction failed, so the UI can say why instead of showing 0 chars.
    error: str | None = Field(default=None)
    #: When this document last reached the retrieval index. `None` means the
    #: vector half does not cover it — attached before the index existed, or
    #: indexed and failed. Lexical search covers it either way.
    indexed_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
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
    #: Shape depends on `kind` and mirrors the discriminated union in types.ts.
    data: dict | None = Field(default=None, sa_column=_json(nullable=True))
    storage_key: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class Job(SQLModel, table=True):
    """Long-running generation that outlives its request.

    Video only: pictures and speech return inside the call that asked for them,
    while a clip takes minutes and the upstream hands back a ticket. This row is
    that ticket, so closing the tab loses nothing.

    `provider_job_id` is the upstream handle and the only way to recover a clip
    after a restart — which is why the row is written before polling starts.
    """

    __tablename__ = "jobs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    kind: str = Field(default="av")
    #: Text, not a database enum: `JobStatus` names the values, and a native
    #: enum would need a migration for every value added.
    status: str = Field(default=JobStatus.queued)
    prompt: str = Field(default="")
    model: str = Field(default="")
    #: Resolution, duration, audio — what the price was quoted from.
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


#: Agents named this enum first and own its Postgres type. Skills share both:
#: a second type with the same two labels would be one more migration to keep
#: in step for nothing.
AgentVisibility = Visibility


class SkillSource(StrEnum):
    built_in = "built-in"
    workspace = "workspace"
    personal = "personal"


class Skill(SQLModel, table=True):
    """An installed procedure that may be activated for one turn.

    `when_to_use` is its own column because it is what the model reads to decide
    whether the skill applies. `enabled` means installed/available; it never
    means "inject this into every prompt".
    """

    __tablename__ = "skills"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    slug: str = Field(default="")
    description: str = Field(default="")
    when_to_use: str = Field(default="")
    #: The procedure itself — the body of SKILL.md.
    body: str = Field(default="")
    #: Stable identity for a shipped skill. Null for user-authored skills.
    catalog_key: str | None = Field(default=None)
    source: SkillSource = Field(default=SkillSource.personal)
    #: Shared to the workspace store, exactly as an agent is. A skill is only
    #: ever *run* out of its owner's account, so sharing means "copyable",
    #: never "usable in place" — see `visibility` on Agent below.
    visibility: Visibility = Field(
        default=Visibility.private,
        sa_column=Column(
            PgEnum(Visibility, name="agentvisibility", create_type=False),
            nullable=False,
            server_default=Visibility.private.value,
        ),
    )
    #: How many accounts took a copy. Written by the install route only.
    installs: int = Field(default=0)
    #: The shared row this one was copied from. Kept so the store can say
    #: "이미 가져옴" without comparing bodies, and so a second install is a
    #: no-op rather than a duplicate.
    origin_id: str | None = Field(default=None, index=True)
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Tool names that must survive the agent's hard allowlist for this skill.
    required_tools: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Approximate prompt cost displayed before a user activates the skill.
    estimated_tokens: int = Field(default=0)
    version: str = Field(default="1.0.0")
    enabled: bool = Field(default=True)
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
    #: One @handle per owner. See `_claim_slug` in routers/workspace.py for the
    #: sentence a duplicate gets; this is the backstop behind it.
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
    temperature: float = Field(default=0.7)
    color: str = Field(default="#5b53e8")
    enabled: bool = Field(default=True)
    #: Named explicitly now that skills share the type: the enum class was
    #: renamed and an inferred name would have drifted off `agentvisibility`.
    visibility: Visibility = Field(
        default=Visibility.private,
        sa_column=Column(
            PgEnum(Visibility, name="agentvisibility", create_type=False),
            nullable=False,
            server_default=Visibility.private.value,
        ),
    )
    #: Names this agent's collection in the retrieval index. Minted on first
    #: use, never derived from `id` — see migration 0015: the collection name is
    #: the whole authorisation over there, and `id` travels too widely to be one.
    index_key: str | None = Field(default=None)
    installs: int = Field(default=0)
    #: Stable identity for an agent shipped in the starter catalogue, matching
    #: `Skill.catalog_key`. Null for anything a person wrote.
    catalog_key: str | None = Field(default=None)
    #: The shared agent this one was copied from. Same contract as on Skill.
    origin_id: str | None = Field(default=None, index=True)
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

    The token *is* the permission. Nothing else is checked on the public route,
    which is why it has to be long, random, and revocable — and why the response
    carries only the shared thing: no owner name, no project, no neighbouring
    artifacts, nothing to walk from.
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
    """Who opened a shared link, and when.

    What can honestly be said about a reader depends on how they arrived. A
    signed-in one has an account, so the row names it. A `link`-scope reader has
    none by design, and their address is the only thing this server ever learns.

    Name and email are copies rather than a join: an account can be renamed or
    deleted, and a log that rewrites itself is not a log. `viewer_id` stays for
    the cases where the live account is what the reader wants.

    One row per reader per hour — twenty refreshes are one visit, and twenty rows
    would bury the other readers.
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
    #: First hop of `X-Forwarded-For`. Empty when the server sits behind a
    #: proxy that strips it — said as empty rather than as a proxy's own IP.
    ip: str = Field(default="")
    #: Raw `User-Agent`. Stored whole and shortened for display: the readable
    #: form drops everything that would matter if the question ever became a
    #: serious one.
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
    """An MCP server this user has installed.

    Credentials never live here — see `ConnectorCredential`. The browser reads
    this table's shape and never the other one.
    """

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
    #: Extra process env for stdio servers. `{{USER_ID}}` etc. are substituted
    #: per caller at spawn time — see services/mcp.py.
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
    """One tool a connector exposes, with its own on/off.

    Per-tool rather than per-server because a server usually mixes reads and
    writes, and "let it read my repos" is a different decision from "let it push".
    """

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
    """Secrets for a connector. Never serialised to the browser, ever."""

    __tablename__ = "connector_credentials"

    connector_id: str = Field(foreign_key="connectors.id", primary_key=True)
    payload: dict | None = Field(default=None, sa_column=_json(nullable=True))
    expires_at: datetime | None = Field(default=None, sa_column=_ts(nullable=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts(nullable=False))


class Template(SQLModel, table=True):
    """A starting point somebody wrote down, so the next person can reuse it.

    The built-in gallery ships as a static array in the frontend and cannot be
    added to — which is fine until an organisation has a form of its own, and
    then the one document everyone actually produces is the one the product has
    no starting point for. This is that gap: same shape as a built-in, owned by
    a user, and optionally carrying a file that *is* the form.
    """

    __tablename__ = "templates"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    #: Which surface it starts — report, slides, chat, image, av.
    kind: str = Field(default="report")
    #: The gallery's filter chip. Free text; the built-ins use 학업/업무/연구.
    group: str = Field(default="내 템플릿")
    title: str
    description: str = Field(default="")
    #: What the person has to bring, shown as chips before they commit.
    fills: list | None = Field(default=None, sa_column=_json(nullable=True))
    #: Ends mid-sentence, where the person takes over. Same rule as built-ins.
    prompt: str = Field(default="")
    #: An uploaded form this template writes *into*. The file's extracted text
    #: is what reaches the model, so the shape of a 공문 survives into the draft.
    file_id: str | None = Field(default=None, foreign_key="files.id", index=True)
    #: The 서식 the result comes out wearing. Plain text and not a foreign key
    #: for the same reason `sessions.render_template_id` is not: the rendering
    #: catalogue lives in the image, not in a table, so a release that retires
    #: a 서식 must leave the row readable rather than break the load.
    #:
    #: Empty means the job has no fixed shape, and then the writing surfaces
    #: choose the colour and the impression from the subject instead.
    render_template_id: str = Field(default="", max_length=60)
    #: Offered to every account. Administrator-only; see migration 0017.
    shared: bool = Field(default=False)
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
]
