"""Workspace: projects, files, artifacts, skills, memories, agents, connectors.

Everything here is owned by exactly one user. The only sharing is an agent's
`visibility` flag; ownership is a plain column, so a real sharing model would
be an ACL table rather than a rework of these rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Index
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
    __tablename__ = "projects"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str = Field(default="")
    emoji: str = Field(default="📁")
    #: Prepended to every turn in this project. The whole point of a project.
    instructions: str = Field(default="")
    skill_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
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
    name: str
    size: int = Field(default=0)
    mime: str = Field(default="")
    storage_key: str = Field(default="")
    text: str = Field(default="")
    #: Rough — length/3.5. Enough to warn before a prompt blows the window.
    tokens: int = Field(default=0)
    #: Set when extraction failed, so the UI can say why instead of showing 0 chars.
    error: str | None = Field(default=None)
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


class SkillSource(StrEnum):
    built_in = "built-in"
    workspace = "workspace"
    personal = "personal"


class Skill(SQLModel, table=True):
    """A named procedure injected into the system turn when enabled.

    `when_to_use` is its own column because it is what the model reads to decide
    whether the skill applies.
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
    source: SkillSource = Field(default=SkillSource.personal)
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
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


class AgentVisibility(StrEnum):
    private = "private"
    org = "org"


class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    name: str
    slug: str = Field(default="")
    description: str = Field(default="")
    model: str = Field(default="")
    system_prompt: str = Field(default="")
    #: Tool names this agent may call. Empty list means "everything available".
    tools: list | None = Field(default=None, sa_column=_json(nullable=True))
    skill_ids: list | None = Field(default=None, sa_column=_json(nullable=True))
    kinds: list | None = Field(default=None, sa_column=_json(nullable=True))
    temperature: float = Field(default=0.7)
    color: str = Field(default="#5b53e8")
    enabled: bool = Field(default=True)
    visibility: AgentVisibility = Field(default=AgentVisibility.private)
    installs: int = Field(default=0)
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


__all__ = [
    "Job",
    "JobStatus",
    "Agent",
    "AgentVisibility",
    "Artifact",
    "ArtifactKind",
    "ArtifactVersion",
    "Connector",
    "ConnectorCredential",
    "ConnectorStatus",
    "ConnectorTool",
    "Memory",
    "MemoryType",
    "Project",
    "Skill",
    "SkillSource",
    "StoredFile",
    "Transport",
]
