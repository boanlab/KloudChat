"""An agent's kept-on skills reach every writing surface they are meant for.

A 날짜 계산 or 견적 계산 kept on in a report agent used to be dropped without a word (its
`kinds` named only chat), and 개조식 보고 in a chat agent likewise. The shared calculation,
research and writing skills now name every surface they help on; on a document they bring
the server step that does their work (pre-pass, code check, deeper search), and saving an
agent says which of its skills still apply on none of its surfaces.
"""

from __future__ import annotations

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.user import User
from app.models.workspace import (
    Agent,
    Connector,
    DesignSystem,
    Memory,
    Project,
    Skill,
    StoredFile,
)
from app.routers import workspace as ws
from app.schemas.workspace import AgentIn
from app.services import (
    process_settings,
    research,
    settings_store,
    skill_packages,
    skill_specs,
    skill_turn,
    starter,
    workspace_context,
)


@pytest.mark.parametrize(("key", "surfaces"), [
    ("date-calc", {"chat", "report"}),
    ("quote-calc", {"chat", "report"}),
    ("calc-check", {"chat", "report", "slides"}),
    ("grounding-check", {"chat", "report", "slides"}),
    ("deep-research", {"chat", "report"}),
    ("source-check", {"chat", "report"}),
    ("copy-editing", {"chat", "report", "slides"}),
    ("generic-language-killer", {"chat", "report"}),
    ("gaejo-briefing", {"chat", "report"}),
])
def test_shared_skills_name_every_writing_surface_they_help_on(key, surfaces):
    assert surfaces <= set(skill_packages.package_for(key).kinds)


def test_a_document_writer_gets_the_server_block_and_a_chat_turn_the_script():
    for key, block in (("date-calc", "확정 날짜(코드 계산)"), ("quote-calc", "견적 확정 블록")):
        package = skill_packages.package_for(key)
        doc = workspace_context.surface_body(package.body, SessionKind.report)
        chat = workspace_context.surface_body(package.body, SessionKind.chat)
        assert block in doc and "run_skill_script" not in doc and "[보고서에서]" not in doc
        assert "run_skill_script" in chat and block not in chat


def test_a_surface_line_may_name_both_documents():
    body = "공통.\n[채팅에서]\n챗만.\n[보고서·슬라이드에서]\n문서만."
    assert workspace_context.surface_body(body, SessionKind.chat) == "공통.\n\n챗만."
    for kind in (SessionKind.report, SessionKind.slides):
        assert workspace_context.surface_body(body, kind) == "공통.\n\n문서만."


def test_a_chat_answer_types_the_gaejo_marks_and_the_report_draws_them():
    package = skill_packages.package_for("gaejo-briefing")
    chat = workspace_context.surface_body(package.body, SessionKind.chat)
    report = workspace_context.surface_body(package.body, SessionKind.report)
    assert "□ for the top level" in chat
    assert "□ for the top level" not in report and "Do not type the symbols" in report


def test_skills_add_their_steps_to_the_templates_instead_of_replacing_them():
    specs = [skill_specs.of_package(skill_packages.package_for(k))
             for k in ("date-calc", "quote-calc", "deep-research")]
    own = skill_turn.merge(specs).process_with(None)
    settings = process_settings.resolve(template_id="doc-comparison", format_id="", own=own)
    # the 서식's own steps stay, the skills' join in the shipped order
    assert settings.prepass == ("date-calc", "quote-calc", "staffing", "compare-cost",
                                "compare-check")
    assert {"quote-totals", "lead-dates", "compare-cells"} <= set(settings.checks)
    assert settings.deep_search
    # the agent's own single settings still win over a skill's
    own = skill_turn.merge(specs).process_with({"deep_search": False})
    assert not process_settings.resolve(template_id=None, format_id="", own=own).deep_search


@pytest.mark.asyncio
async def test_deep_search_doubles_the_queries_and_the_sources(monkeypatch):
    asked: dict = {}

    async def backends():
        return settings_store.ToolBackends(search="http://search.test")

    async def plan(_request, _model, _key, *, scholarly=False, most=research.MAX_QUERIES):
        asked["most"] = most
        return ["q"], {"inputTokens": 0, "outputTokens": 0}

    async def gather(*_args, **_kwargs):
        return []

    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(research, "_plan", plan)
    monkeypatch.setattr(research, "_gather", gather)
    await research.run("AI 반도체 동향", deep=True)
    assert asked["most"] == research.MAX_QUERIES * 2
    await research.run("AI 반도체 동향")
    assert asked["most"] == research.MAX_QUERIES


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
    models = (User, Agent, Skill, StoredFile, Memory, ChatSession, Project, DesignSystem,
              Connector)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all,
                                tables=[metadata.tables[m.__tablename__] for m in models])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _member(db) -> User:
    user = User(email="surface@example.com", name="회원", password_hash="x")
    db.add(user)
    await db.commit()
    return user


async def _shipped(db, user: User, *keys: str) -> list[Skill]:
    """The person's copies of shipped skills, as the catalogue seeds them."""
    rows = []
    for key in keys:
        package = skill_packages.package_for(key)
        rows.append(Skill(owner_id=user.id, name=package.name, body=package.body,
                          catalog_key=key, kinds=list(package.kinds),
                          required_tools=list(package.required_tools)))
    db.add_all(rows)
    await db.commit()
    return rows


@pytest.mark.asyncio
async def test_a_report_agent_keeps_date_and_quote_calculation_on(db):
    user = await _member(db)
    dates, quotes = await _shipped(db, user, "date-calc", "quote-calc")
    agent = Agent(owner_id=user.id, name="구매·도입 검토", kinds=["report"], surface="report",
                  active_skill_ids=[dates.id, quotes.id])
    session = ChatSession(user_id=user.id, kind=SessionKind.report, agent_id=agent.id)
    db.add_all([agent, session])
    await db.commit()

    rows = await workspace_context.agent_skill_rows(db, user.id, agent, "report")
    assert [r.id for r in rows] == [dates.id, quotes.id]
    # applied with no tools on the writer: what reaches the prompt is the block to copy from
    applied = await workspace_context._agent_skills(db, user, session, agent, [], set(), False)
    assert [s.id for s, _ in applied] == [dates.id, quotes.id]
    text = "\n".join(b.text for b in workspace_context._skill_blocks(applied, session.kind))
    assert "견적 확정 블록" in text and "run_skill_script" not in text
    # and they bring the pre-pass and the code check that do the counting
    found = await skill_turn.brought(db, user, session, agent, [])
    settings = process_settings.resolve(template_id=None, format_id="",
                                        own=found.process_with(None))
    assert {"date-calc", "quote-calc"} <= set(settings.prepass)
    assert "quote-totals" in settings.checks


@pytest.mark.asyncio
async def test_saving_an_agent_names_the_skills_none_of_its_surfaces_run(db):
    user = await _member(db)
    gaejo, dates = await _shipped(db, user, "gaejo-briefing", "date-calc")
    own = Skill(owner_id=user.id, name="보도자료 틀", body="x", kinds=["report"])
    deck = Skill(owner_id=user.id, name="장표 구성", body="x", kinds=["slides"])
    db.add_all([own, deck])
    await db.commit()

    out = await ws.create_agent(AgentIn(name="영상 학습 노트", kinds=["chat"],
                                        active_skill_ids=[gaejo.id, own.id],
                                        skill_ids=[dates.id, deck.id]), user, db)
    # saved as asked (a warning, never an error)
    assert out.active_skill_ids == [gaejo.id, own.id]
    warned = [(w.skill_id, w.place, w.skill_kinds) for w in out.skill_warnings]
    assert warned == [(own.id, "active", ["report"]), (deck.id, "available", ["slides"])]
    assert "「보도자료 틀」 스킬은 보고서 화면에서만" in out.skill_warnings[0].message
    assert "skillWarnings" in out.model_dump(by_alias=True)

    # moving the agent onto reports clears one and raises the other
    out = await ws.patch_agent(out.id, AgentIn(name="영상 학습 노트", kinds=["report"],
                                                       surface="report"), user, db)
    assert [w.skill_id for w in out.skill_warnings] == [deck.id]

    # an agent with no surfaces named runs everywhere: nothing to warn about
    out = await ws.patch_agent(out.id, AgentIn(name="영상 학습 노트", kinds=[]), user, db)
    assert out.skill_warnings == []


def test_every_shipped_skill_still_seeds():
    # the widened packages keep valid front matter (process, kinds) for the catalogue
    for key in ("date-calc", "quote-calc", "deep-research", "gaejo-briefing"):
        spec = next(s for s in starter._SKILLS if s["key"] == key)
        assert list(spec["kinds"]) == list(skill_packages.package_for(key).kinds)
