"""The shipped agents (lineup): their specs hold together and the seeder makes them.

Seven are withdrawn (D21: their abilities are skills now) — offered to no one anew, their rows,
copies, conversations and bots kept working; two are retired and kept only so the
conversations made with them still open."""

from __future__ import annotations

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.publishing import Publication
from app.models.user import User, UserRole
from app.models.workspace import Agent, DesignSystem, Memory, Skill, StoredFile, Visibility
from app.routers import workspace as ws
from app.services import design_templates, doc_formats, starter, workspace_context
from app.services.tools import registry

_SHIPPED = {
    "regulation-guide": "규정·업무 안내",
    "gaejo-report": "개조식 보고",
    "comparison-report": "비교·결정",
    "data-analysis": "자료 추출·집계",
    "minutes": "회의록",
    "document": "공문·기안 초안",
    "slides": "발표 자료",
    "career-docs": "자기소개서·경력기술서",
    "client-proposal": "고객 맞춤 제안서",
}
_OFFERED = ["regulation-guide", "gaejo-report", "comparison-report", "data-analysis", "minutes",
            "document", "slides"]
_DRAFTS = {"document", "slides"}
_RETIRED = {"career-docs", "client-proposal"}
_FORMER = {"gaejo-report": ["개조식 보고서", "요약·정리"], "minutes": ["기록 정리"],
           "comparison-report": ["비교·분석 보고서"], "data-analysis": ["데이터 분석"],
           "document": ["문서 작성"]}
_BINDINGS = {
    "regulation-guide": ("chat", None, None, "기본"),
    "client-proposal": ("report", "doc-client-proposal", "client_proposal", "기본"),
    "gaejo-report": ("report", "doc-gaejo-report", "gaejo", "문서용 명조"),
    "document": ("report", None, None, "기본"),
    "minutes": ("report", "doc-minutes", "minutes", "문서용 명조"),
    "data-analysis": ("chat", None, None, "기본"),
    "slides": ("slides", None, None, "발표용 청록"),
    "career-docs": ("chat", "doc-career-history", "career_history", "기본"),
    "comparison-report": ("report", "doc-comparison", "comparison", "기본"),
}
_TOOLS = {
    "regulation-guide": ["search_knowledge", "calculate", "run_skill_script"],
    "data-analysis": ["run_code", "create_chart", "calculate"],
    "career-docs": ["run_skill_script", "calculate"],
}
_BY_KEY = {spec["key"]: spec for spec in starter._AGENTS}


def test_the_catalogue_ships_the_lineup_agents_in_order():
    assert [spec["key"] for spec in starter._AGENTS] == list(_SHIPPED)
    assert {key: spec["name"] for key, spec in _BY_KEY.items()} == _SHIPPED
    assert starter.offered_agents(drafts=True) == [] and starter.withdrawn_agents() == set(
        _OFFERED)
    assert [s["key"] for s in starter.offered_agents(drafts=True, withdrawn=True)] == _OFFERED
    assert [s["key"] for s in starter.offered_agents(withdrawn=True)] == [
        k for k in _OFFERED if k not in _DRAFTS]
    assert starter.draft_agents() == _DRAFTS and starter.retired_agents() == _RETIRED
    assert {k: s.get("former_names") for k, s in _BY_KEY.items() if s.get("former_names")} \
        == _FORMER
    for spec in starter._AGENTS:
        assert spec["description"] and spec["guide"] and spec["starters"]
        assert len(spec["system_prompt"]) > 400


@pytest.mark.parametrize("key", list(_SHIPPED))
def test_each_agent_names_its_tools_and_they_exist(key):
    spec = _BY_KEY[key]
    # Explicit, never inherited: report surfaces deny every chat tool.
    assert "tools" in spec
    assert spec["tools"] == _TOOLS.get(key, [])
    known = {
        tool.name
        for tool in (
            registry.CALCULATE, registry.RUN_CODE, registry.RUN_SKILL_SCRIPT, registry.CREATE_CHART
        )
    } | {"search_knowledge"}
    assert set(spec["tools"]) <= known


@pytest.mark.parametrize("key", list(_SHIPPED))
def test_each_agents_bindings_agree_with_its_kinds_templates_and_formats(key):
    spec = _BY_KEY[key]
    surface, template_id, format_id, design = _BINDINGS[key]
    assert (spec["surface"], spec.get("template"), spec.get("doc_format"), spec["design"]) == (
        surface, template_id, format_id, design
    )
    assert spec["surface"] in spec["kinds"]
    if template_id:
        template = design_templates.get(template_id)
        assert template is not None and template.kind in design_templates.HTML_KINDS
        assert template.surface.value in spec["kinds"]
        assert doc_formats.TEMPLATE_FORMATS[template_id] == format_id
    if format_id:
        assert format_id in doc_formats.BY_ID and "report" in spec["kinds"]
    assert design in {row["name"] for row in starter._DESIGNS}
    skills = {row["key"]: row for row in starter._SKILLS}
    for skill_key in spec["skills"]:
        skill = skills[skill_key]
        # A skill the agent's own tools cannot run would be refused when switched on.
        assert set(skill.get("required_tools") or []) <= set(spec["tools"])
        assert set(skill["kinds"]) & set(spec["kinds"])
    own = {"career-docs": ["text-fit"], "regulation-guide": ["date-calc"]}
    assert spec["skills"] == own.get(key, [])


def test_career_docs_reads_its_surface_part_only():
    prompt = _BY_KEY["career-docs"]["system_prompt"]
    chat = workspace_context.surface_prompt(prompt, "chat")
    report = workspace_context.surface_prompt(prompt, "report")
    common = "Do not write falsehoods; they work against the applicant"
    assert common in chat and common in report
    assert "text-fit" in chat and "「[제출용 답안 n]」" in chat
    assert "text-fit" not in report and "제출용 답안" not in report
    assert "‘병행 활동’" in report and "‘병행 활동’" not in chat
    assert "[채팅에서]" not in chat + report and "[보고서에서]" not in chat + report
    # A prompt with no surface parts reads the same everywhere.
    plain = "너는 회의록을 쓴다."
    assert workspace_context.surface_prompt(plain, "report") == plain


# ── seeding ────────────────────────────────────────────────────────────


@pytest.fixture
async def db():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                metadata.create_all,
                tables=[
                    metadata.tables[model.__tablename__]
                    for model in (User, Agent, Skill, DesignSystem, StoredFile, Memory, ChatSession,
                                  Publication)
                ],
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _admin(db) -> User:
    admin = User(email="lineup-admin@example.com", name="Admin", password_hash="x",
                 role=UserRole.admin)
    db.add(admin)
    await db.commit()
    await starter.seed_designs(db, admin.id)
    await db.commit()
    return admin


@pytest.mark.asyncio
async def test_the_seeder_makes_the_six_agents_for_the_catalogue_owner(db):
    admin = await _admin(db)
    # An agent of the owner's own with a shipped name keeps its handle.
    own = Agent(owner_id=admin.id, name="회의록", slug="회의록", system_prompt="내 회의록")
    db.add(own)
    await db.commit()

    await starter.sync_catalog(db, admin)
    await db.commit()
    rows = {
        row.catalog_key: row
        for row in (await db.exec(select(Agent).where(Agent.catalog_key.is_not(None)))).all()
    }
    # A retired agent is never seeded afresh.
    assert set(rows) == set(_OFFERED)
    designs = {
        row.id: row.name
        for row in (await db.exec(select(DesignSystem))).all()
    }
    skills = {row.id: row.catalog_key for row in (await db.exec(select(Skill))).all()}
    for key, agent in rows.items():
        assert not agent.retired
        spec = _BY_KEY[key]
        surface, template_id, format_id, design = _BINDINGS[key]
        assert agent.owner_id == admin.id
        assert agent.visibility is Visibility.org
        assert agent.name == _SHIPPED[key]
        assert agent.tools == _TOOLS.get(key, [])
        assert (agent.surface, agent.render_template_id, agent.doc_format) == (
            surface, template_id, format_id
        )
        assert designs[agent.design_system_id] == design
        assert [skills[i] for i in agent.skill_ids] == spec["skills"]
        assert agent.system_prompt == spec["system_prompt"]
        # What the agent API would accept for these values.
        await ws._validate_agent_bindings(db, admin, {
            "kinds": agent.kinds, "surface": agent.surface,
            "render_template_id": agent.render_template_id, "doc_format": agent.doc_format,
            "design_system_id": agent.design_system_id,
        })
    assert rows["minutes"].slug == "회의록-2" and own.slug == "회의록"
    # Idempotent.
    assert await starter.seed_catalog(db, admin.id) == 0


@pytest.mark.asyncio
async def test_a_career_docs_report_session_gets_the_report_part_of_the_prompt(db):
    """Retired, the agent's old conversations still run on its prompt parts."""
    admin = await _admin(db)
    spec = _BY_KEY["career-docs"]
    agent = Agent(owner_id=admin.id, name=spec["name"], slug="career", catalog_key="career-docs",
                  system_prompt=spec["system_prompt"], kinds=spec["kinds"], retired=True)
    db.add(agent)
    await db.commit()
    await starter.sync_catalog(db, admin)
    await db.commit()
    for kind, present, absent in (
        (SessionKind.chat, "text-fit", "‘병행 활동’"),
        (SessionKind.report, "‘병행 활동’", "text-fit"),
    ):
        session = ChatSession(user_id=admin.id, agent_id=agent.id, kind=kind)
        db.add(session)
        await db.commit()
        context = await workspace_context.assemble(db, admin, session)
        role = next(b.text for b in context.blocks if b.source == "agent.instructions")
        assert present in role and absent not in role


@pytest.mark.asyncio
async def test_a_user_running_a_seeded_agent_wears_their_own_design_of_that_name(db, monkeypatch):
    admin = await _admin(db)
    await starter.sync_catalog(db, admin)
    user = User(email="lineup-user@example.com", name="User", password_hash="x")
    bare = User(email="lineup-bare@example.com", name="Bare", password_hash="x")
    db.add_all([user, bare])
    await db.commit()
    await starter.seed_designs(db, user.id)
    await db.commit()
    agent = (await db.exec(select(Agent).where(Agent.catalog_key == "gaejo-report"))).one()
    own = (
        await db.exec(
            select(DesignSystem).where(
                DesignSystem.owner_id == user.id, DesignSystem.name == "문서용 명조"
            )
        )
    ).one()
    assert agent.design_system_id != own.id

    # The shared original and a copy installed before it was withdrawn both resolve to the
    # user's own row.
    with monkeypatch.context() as before:
        before.setattr(starter, "withdrawn_agents", frozenset)
        copy = await ws.install_agent(agent.id, user, db)
    for agent_id in (agent.id, copy.id):
        session = ChatSession(user_id=user.id, agent_id=agent_id, kind=SessionKind.report)
        db.add(session)
        await db.commit()
        design = await workspace_context.design_for(db, user, session)
        assert design is not None and design.id == own.id
    listed = {row.id: row for row in await ws.list_agents(user, db)}
    assert agent.id not in listed and listed[copy.id].design_name == "문서용 명조"

    # Someone holding no design of that name gets none.
    session = ChatSession(user_id=bare.id, agent_id=agent.id, kind=SessionKind.report)
    db.add(session)
    await db.commit()
    assert await workspace_context.design_for(db, bare, session) is None


@pytest.mark.asyncio
async def test_retiring_hides_the_agent_and_its_copies_without_deleting_them(db):
    admin = await _admin(db)
    user = User(email="lineup-taker@example.com", name="Taker", password_hash="x")
    db.add(user)
    await db.commit()
    # The catalogue as it was before the retirement: the agent offered and taken.
    spec = {**_BY_KEY["client-proposal"], "retired": False}
    import app.services.starter as module

    real = list(module._AGENTS)
    module._AGENTS[:] = [s if s["key"] != "client-proposal" else spec for s in real]
    try:
        await starter.sync_catalog(db, admin)
        await db.commit()
        original = (await db.exec(
            select(Agent).where(Agent.catalog_key == "client-proposal"))).one()
        original_id = original.id
        copy = await ws.install_agent(original_id, user, db)
        session = ChatSession(user_id=user.id, agent_id=copy.id, kind=SessionKind.report)
        db.add(session)
        await db.commit()
        session_id = session.id
    finally:
        module._AGENTS[:] = real
    await starter.sync_catalog(db, admin)
    await db.commit()
    db.expire_all()
    for person in (admin, user):
        await db.refresh(person)
    kept = await db.get(Agent, copy.id)
    assert kept is not None and kept.retired
    assert (await db.get(Agent, original_id)).retired
    # Listed nowhere, installed by nobody, starting nothing new; the old conversation stays.
    assert copy.id not in {a.id for a in await ws.list_agents(user, db)}
    assert original_id not in {a.id for a in await ws.list_agents(admin, db)}
    from fastapi import HTTPException

    from app.routers import sessions as sessions_router
    from app.schemas.chat import SessionCreate

    with pytest.raises(HTTPException) as caught:
        await sessions_router.create_session(SessionCreate(agent_id=copy.id), user, db)
    assert caught.value.status_code == 410
    other = User(email="lineup-other@example.com", name="Other", password_hash="x")
    db.add(other)
    await db.commit()
    original_row = await db.get(Agent, original_id)
    original_row.visibility = Visibility.org
    db.add(original_row)
    await db.commit()
    with pytest.raises(HTTPException) as caught:
        await ws.install_agent(original_id, other, db)
    assert caught.value.status_code == 410
    assert await db.get(ChatSession, session_id) is not None


@pytest.mark.asyncio
async def test_drafts_are_for_administrators_and_renamed_copies_follow(db, monkeypatch):
    admin = await _admin(db)
    user = User(email="lineup-reader@example.com", name="Reader", password_hash="x")
    db.add(user)
    await db.commit()
    await starter.sync_catalog(db, admin)
    await db.commit()
    # Withdrawn: listed to no one and installed by no one, drafts included.
    assert not {a.catalog_key for a in await ws.list_agents(user, db) if a.catalog_key}
    assert not {a.catalog_key for a in await ws.list_agents(admin, db) if a.catalog_key}
    minutes = (await db.exec(select(Agent).where(Agent.catalog_key == "minutes"))).one()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        await ws.install_agent(minutes.id, user, db)
    assert caught.value.status_code == 410

    # As before the withdrawal: drafts for administrators, copies taken by members.
    monkeypatch.setattr(starter, "withdrawn_agents", frozenset)
    seen = {a.catalog_key for a in await ws.list_agents(user, db) if a.catalog_key}
    assert seen == set(_OFFERED) - _DRAFTS
    assert {a.catalog_key for a in await ws.list_agents(admin, db) if a.catalog_key} \
        == set(_OFFERED)
    draft = (await db.exec(select(Agent).where(Agent.catalog_key == "document"))).one()
    with pytest.raises(HTTPException) as caught:
        await ws.install_agent(draft.id, user, db)
    assert caught.value.status_code == 404

    copy = await ws.install_agent(minutes.id, user, db)
    renamed = await ws.install_agent(
        (await db.exec(select(Agent).where(Agent.catalog_key == "gaejo-report"))).one().id,
        user, db)
    data = await ws.install_agent(
        (await db.exec(select(Agent).where(Agent.catalog_key == "data-analysis"))).one().id,
        user, db)
    monkeypatch.undo()
    for row_id, old in ((copy.id, "기록 정리"), (renamed.id, "내 개조식"),
                        (data.id, "데이터 분석")):
        row = await db.get(Agent, row_id)
        row.name = old
        db.add(row)
    await db.commit()
    await starter.sync_catalog(db, admin)
    await db.commit()
    db.expire_all()
    # An untouched earlier name follows the rename; a name of the person's own stays.
    assert (await db.get(Agent, copy.id)).name == "회의록"
    assert (await db.get(Agent, renamed.id)).name == "내 개조식"
    assert (await db.get(Agent, data.id)).name == "자료 추출·집계"
    # The copies stay the member's, listed and runnable.
    await db.refresh(user)
    mine = {a.id for a in await ws.list_agents(user, db)}
    assert {copy.id, renamed.id, data.id} <= mine
