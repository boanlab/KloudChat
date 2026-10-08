"""Agent bindings: the surface, 서식, format and design an agent opens with, and the
handoff from an agent chat to its document."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, Role, SessionKind, TurnFailure
from app.models.user import User
from app.models.workspace import (
    Agent,
    Connector,
    DesignSystem,
    Memory,
    Project,
    Skill,
    StoredFile,
    Visibility,
)
from app.routers import sessions as sessions_router
from app.routers import workspace as ws
from app.schemas.chat import HandoffIn, SessionCreate
from app.schemas.workspace import AgentIn
from app.services import doc_formats, workspace_context
from app.services import files as file_service

# ── migration ──────────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0052_agent_bindings.py"
    spec = importlib.util.spec_from_file_location("agent_bindings_0052", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_adds_and_drops_the_binding_columns_on_sqlite():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0052", "0051")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE agents (id VARCHAR PRIMARY KEY, name VARCHAR)"))
        conn.execute(sa.text("INSERT INTO agents (id, name) VALUES ('a1', 'old')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("agents")}
        assert {"surface", "render_template_id", "doc_format", "design_system_id"} <= columns
        # An existing agent keeps opening the way it did.
        row = conn.execute(sa.text("SELECT surface, render_template_id FROM agents")).one()
        assert tuple(row) == (None, None)
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("agents")}
        assert columns == {"id", "name"}


# ── an in-memory database with the real models ─────────────────────────


@pytest.fixture
async def db():
    # Adapt only a copied schema: PostgreSQL JSON defaults are not SQLite SQL.
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    models = (
        User, Agent, Skill, StoredFile, Memory, ChatSession, Message, Project, DesignSystem,
        Connector,
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                metadata.create_all,
                tables=[metadata.tables[model.__tablename__] for model in models],
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _every_surface_on(monkeypatch, tmp_path):
    async def enabled():
        return [kind.value for kind in SessionKind]

    monkeypatch.setattr(sessions_router.settings_store, "enabled_kinds", enabled)
    monkeypatch.setattr(file_service, "storage_root", lambda: tmp_path)


async def _people(db):
    author = User(email="author@example.com", name="Author", password_hash="x")
    taker = User(email="taker@example.com", name="Taker", password_hash="x")
    db.add_all([author, taker])
    await db.commit()
    design = DesignSystem(owner_id=author.id, name="비즈니스", tokens={"accent": "#1f3a5f"})
    db.add(design)
    await db.commit()
    return author, taker, design


def _proposal_agent(design_id: str | None = None, **overrides) -> AgentIn:
    fields = {
        "name": "고객 맞춤 제안서",
        "system_prompt": "너는 고객 맞춤 제안서를 쓴다.",
        "kinds": ["report"],
        "surface": "report",
        "render_template_id": "doc-client-proposal",
        "doc_format": "client_proposal",
        "design_system_id": design_id,
        "tools": [],
    }
    fields.update(overrides)
    return AgentIn(**fields)


# ── create, session, copy ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_session_started_from_an_agent_takes_its_bindings(db):
    author, _, design = await _people(db)
    out = await ws.create_agent(_proposal_agent(design.id), author, db)
    assert (out.surface, out.render_template_id, out.doc_format, out.design_system_id) == (
        "report",
        "doc-client-proposal",
        "client_proposal",
        design.id,
    )

    # No kind named: the agent's surface and 서식.
    session = await sessions_router.create_session(SessionCreate(agent_id=out.id), author, db)
    assert session.kind is SessionKind.report
    assert session.render_template_id == "doc-client-proposal"

    row = await db.get(ChatSession, session.id)
    context = await workspace_context.assemble(db, author, row)
    assert context.doc_format == "client_proposal"
    assert context.design_tokens is not None and context.design_tokens["accent"] == "#1f3a5f"
    assert doc_formats.detect("아무 문서", row.render_template_id, context.doc_format).id == (
        "client_proposal"
    )


@pytest.mark.asyncio
async def test_the_agents_template_and_design_beat_the_projects(db):
    author, _, design = await _people(db)
    mine = DesignSystem(owner_id=author.id, name="문서용 명조", tokens={"accent": "#334155"})
    project = Project(
        user_id=author.id, name="영업", render_templates={"report": "doc-proposal"}
    )
    db.add_all([mine, project])
    await db.commit()
    project.design_system_id = mine.id
    db.add(project)
    await db.commit()
    agent = await ws.create_agent(_proposal_agent(design.id), author, db)

    session = await sessions_router.create_session(
        SessionCreate(agent_id=agent.id, project_id=project.id), author, db
    )
    assert session.render_template_id == "doc-client-proposal"
    context = await workspace_context.assemble(db, author, await db.get(ChatSession, session.id))
    assert context.design_tokens["accent"] == "#1f3a5f"

    # Without the agent the project's 서식 and design stay.
    plain = await sessions_router.create_session(
        SessionCreate(kind=SessionKind.report, project_id=project.id), author, db
    )
    assert plain.render_template_id == "doc-proposal"
    context = await workspace_context.assemble(db, author, await db.get(ChatSession, plain.id))
    assert context.design_tokens["accent"] == "#334155"

    # An agent bound to no design leaves the project's.
    bare = await ws.create_agent(_proposal_agent(None, name="빈 디자인"), author, db)
    session = await sessions_router.create_session(
        SessionCreate(agent_id=bare.id, project_id=project.id), author, db
    )
    context = await workspace_context.assemble(db, author, await db.get(ChatSession, session.id))
    assert context.design_tokens["accent"] == "#334155"


@pytest.mark.asyncio
async def test_a_kind_the_caller_names_wins_and_a_template_of_another_surface_is_not_worn(db):
    author, _, _ = await _people(db)
    agent = await ws.create_agent(
        _proposal_agent(
            name="자기소개서·경력기술서",
            kinds=["chat", "report"],
            surface="chat",
            render_template_id="doc-career-history",
            doc_format=None,
            tools=["run_skill_script", "calculate"],
        ),
        author,
        db,
    )
    opened = await sessions_router.create_session(SessionCreate(agent_id=agent.id), author, db)
    assert opened.kind is SessionKind.chat and opened.render_template_id is None
    report = await sessions_router.create_session(
        SessionCreate(agent_id=agent.id, kind=SessionKind.report), author, db
    )
    assert report.kind is SessionKind.report
    assert report.render_template_id == "doc-career-history"
    context = await workspace_context.assemble(db, author, await db.get(ChatSession, report.id))
    # The 서식 fixes the format; the agent named none.
    assert context.doc_format is None
    assert doc_formats.detect("", report.render_template_id).id == "career_history"


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"surface": "slides"}, "agent_surface_not_in_kinds"),
        ({"surface": "nowhere"}, "agent_surface_unknown"),
        ({"render_template_id": "deck-briefing"}, "design_template_surface_mismatch"),
        ({"render_template_id": "doc-nothing"}, "design_template_not_found"),
        ({"doc_format": "sonnet"}, "doc_format_unknown"),
        (
            {"kinds": ["chat"], "surface": "chat", "render_template_id": None},
            "doc_format_needs_report",
        ),
    ],
)
@pytest.mark.asyncio
async def test_bindings_that_contradict_the_surfaces_are_refused(db, overrides, detail):
    author, _, _ = await _people(db)
    with pytest.raises(HTTPException) as caught:
        await ws.create_agent(_proposal_agent(**overrides), author, db)
    assert caught.value.detail == detail


@pytest.mark.asyncio
async def test_blank_bindings_are_stored_as_none_and_a_patch_is_checked_whole(db):
    author, _, _ = await _people(db)
    agent = await ws.create_agent(
        _proposal_agent(surface="", render_template_id="", doc_format=""), author, db
    )
    assert (agent.surface, agent.render_template_id, agent.doc_format) == (None, None, None)
    patched = await ws.patch_agent(
        agent.id,
        AgentIn.model_validate({"name": agent.name, "renderTemplateId": "doc-comparison"}),
        author,
        db,
    )
    assert patched.render_template_id == "doc-comparison"
    # Narrowing the surfaces past the bound 서식 is refused.
    with pytest.raises(HTTPException) as caught:
        await ws.patch_agent(
            agent.id, AgentIn.model_validate({"name": agent.name, "kinds": ["chat"]}), author, db
        )
    assert caught.value.detail == "design_template_surface_mismatch"


@pytest.mark.asyncio
async def test_a_copy_keeps_the_binding(db):
    author, taker, design = await _people(db)
    design.shared = True
    db.add(design)
    await db.commit()
    original = await ws.create_agent(
        _proposal_agent(design.id, visibility=Visibility.org), author, db
    )
    copy = await ws.install_agent(original.id, taker, db)
    assert copy.id != original.id
    assert (copy.surface, copy.render_template_id, copy.doc_format, copy.design_system_id) == (
        "report",
        "doc-client-proposal",
        "client_proposal",
        design.id,
    )
    session = await sessions_router.create_session(SessionCreate(agent_id=copy.id), taker, db)
    assert session.kind is SessionKind.report
    assert session.render_template_id == "doc-client-proposal"
    context = await workspace_context.assemble(db, taker, await db.get(ChatSession, session.id))
    assert context.design_tokens["accent"] == "#1f3a5f"


@pytest.mark.asyncio
async def test_a_design_the_taker_cannot_see_is_ignored_not_an_error(db):
    author, taker, design = await _people(db)
    original = await ws.create_agent(
        _proposal_agent(design.id, visibility=Visibility.org), author, db
    )
    copy = await ws.install_agent(original.id, taker, db)
    session = await sessions_router.create_session(SessionCreate(agent_id=copy.id), taker, db)
    context = await workspace_context.assemble(db, taker, await db.get(ChatSession, session.id))
    assert context.design_tokens is None


@pytest.mark.asyncio
async def test_deleting_a_design_detaches_the_agents_using_it(db):
    author, _, design = await _people(db)
    agent = await ws.create_agent(_proposal_agent(design.id), author, db)
    await ws.delete_design(design.id, author, db)
    row = await db.get(Agent, agent.id)
    await db.refresh(row)
    assert row.design_system_id is None


# ── handoff ────────────────────────────────────────────────────────────


async def _career_chat(db, author):
    agent = await ws.create_agent(
        _proposal_agent(
            name="자기소개서·경력기술서",
            kinds=["chat", "report"],
            surface="chat",
            render_template_id="doc-career-history",
            doc_format=None,
            tools=["run_skill_script", "calculate"],
        ),
        author,
        db,
    )
    chat = await sessions_router.create_session(SessionCreate(agent_id=agent.id), author, db)
    upload = StoredFile(user_id=author.id, session_id=chat.id, name="이력.txt", text="2019.03 입사")
    db.add(upload)
    await db.commit()
    db.add_all(
        [
            Message(
                session_id=chat.id,
                role=Role.user,
                content="자기소개서 1번 문항 700자로 써 줘",
                attachments=[{"id": upload.id, "name": "이력.txt"}],
            ),
            Message(session_id=chat.id, role=Role.assistant, content="1번 문항 답입니다."),
            Message(session_id=chat.id, role=Role.user, content="  "),
        ]
    )
    await db.commit()
    return agent, chat, upload


@pytest.mark.asyncio
async def test_an_agent_chat_hands_off_to_its_report_keeping_the_agent_and_the_talk(db):
    author, _, _ = await _people(db)
    agent, chat, upload = await _career_chat(db, author)

    out = await sessions_router.handoff_session(chat.id, HandoffIn(kind="report"), author, db)
    assert out.session.kind is SessionKind.report
    assert out.session.agent_id == agent.id
    assert out.session.render_template_id == "doc-career-history"
    transcript, carried = out.attachments
    assert transcript.name == "대화 기록.txt"
    stored = await db.get(StoredFile, transcript.id)
    assert stored.session_id == out.session.id
    assert "[사용자]\n자기소개서 1번 문항 700자로 써 줘" in stored.text
    assert "[답변]\n1번 문항 답입니다." in stored.text
    assert carried.id == upload.id
    # The chat itself is untouched.
    assert (await db.get(ChatSession, chat.id)).kind is SessionKind.chat


@pytest.mark.asyncio
async def test_a_surface_the_agent_does_not_offer_is_refused(db):
    author, _, _ = await _people(db)
    _, chat, _ = await _career_chat(db, author)
    with pytest.raises(HTTPException) as caught:
        await sessions_router.handoff_session(chat.id, HandoffIn(kind="slides"), author, db)
    assert caught.value.detail == "agent_kind_mismatch"


@pytest.mark.asyncio
async def test_only_a_chat_hands_off(db):
    author, _, _ = await _people(db)
    agent = await ws.create_agent(_proposal_agent(), author, db)
    report = await sessions_router.create_session(SessionCreate(agent_id=agent.id), author, db)
    with pytest.raises(HTTPException) as caught:
        await sessions_router.handoff_session(report.id, HandoffIn(kind="report"), author, db)
    assert caught.value.detail == "handoff_from_chat_only"


def test_the_transcript_keeps_the_latest_turns_within_its_bound():
    turns = [
        Message(session_id="s", role=(Role.user, Role.assistant)[i % 2], content=f"턴{i} " * 900)
        for i in range(40)
    ]
    text = sessions_router.handoff_transcript(turns)
    assert len(text) <= sessions_router._HANDOFF_CHARS + 200
    assert "턴39" in text and "턴0 " not in text
    assert sessions_router.handoff_transcript([]) == ""
    failed = Message(session_id="s", role=Role.user, content="실패", failure=TurnFailure.no_answer)
    assert sessions_router.handoff_transcript([failed]) == ""
