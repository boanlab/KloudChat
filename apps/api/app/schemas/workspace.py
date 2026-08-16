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
    Memory,
    MemoryType,
    Project,
    Share,
    ShareScope,
    Skill,
    SkillSource,
    StoredFile,
    Template,
    Transport,
)
from app.schemas.auth import Wire

#: JSONB list columns are nullable in the database, but the wire contract is a
#: list either way — absorbed here rather than in every `of()` and consumer.
JsonList = Annotated[list[str], BeforeValidator(lambda v: v or [])]


# ── files ──────────────────────────────────────────────────────────────
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
        out.files = [FileOut.of(f) for f in (files or [])]
        out.session_ids = list(session_ids or [])
        return out


class ProjectIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    emoji: str = "📁"
    instructions: str = ""
    skill_ids: list[str] | None = None


class ProjectPatch(Wire):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = None
    emoji: str | None = None
    instructions: str | None = None
    skill_ids: list[str] | None = None


# ── artifacts ──────────────────────────────────────────────────────────
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

    @classmethod
    def of(cls, a: Artifact) -> ArtifactOut:
        return cls.model_validate(a, from_attributes=True)


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


class SectionRewrite(Wire):
    """Which section, and why it is being rewritten."""

    section_id: str
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
    source: SkillSource
    kinds: JsonList = Field(default_factory=list)
    version: str
    enabled: bool
    updated_at: datetime

    @classmethod
    def of(cls, s: Skill) -> SkillOut:
        out = cls.model_validate(s, from_attributes=True)
        out.kinds = list(s.kinds or [])
        return out


class SkillIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    when_to_use: str = ""
    body: str = ""
    kinds: list[str] | None = None
    enabled: bool = True


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
    tools: JsonList = Field(default_factory=list)
    skill_ids: JsonList = Field(default_factory=list)
    kinds: JsonList = Field(default_factory=list)
    temperature: float
    color: str
    enabled: bool
    visibility: AgentVisibility
    installs: int
    runs: int
    #: Who made it. The store mixes other people's agents with your own, and
    #: without this the edit button's outcome is unknowable until it 403s.
    owner_id: str
    owner_name: str = ""
    updated_at: datetime

    @classmethod
    def of(cls, a: Agent, owner_name: str = "") -> AgentOut:
        out = cls.model_validate(a, from_attributes=True)
        out.owner_name = owner_name
        out.tools = list(a.tools or [])
        out.skill_ids = list(a.skill_ids or [])
        out.kinds = list(a.kinds or [])
        return out


class AgentIn(Wire):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    model: str = ""
    system_prompt: str = ""
    tools: list[str] | None = None
    skill_ids: list[str] | None = None
    kinds: list[str] | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)
    color: str = "#5b53e8"
    enabled: bool = True
    visibility: AgentVisibility = AgentVisibility.private


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


class KnowledgeUrl(Wire):
    """A page to read into an agent's shelf."""

    url: str = Field(min_length=8, max_length=2000)
