"""A catalogue agent end to end: seeded, installed, refreshed, assembled and retired.

The product ships no agents for now; a test-local spec keeps the mechanism covered.
"""

from __future__ import annotations

import pytest
from catalog_text_skills import use_text_skills
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.publishing import Publication
from app.models.user import User, UserRole
from app.models.workspace import Agent, Memory, Skill, StoredFile, Visibility
from app.routers import workspace as ws
from app.services import starter, workspace_context
from app.services.context import build_messages

#: Shaped like a practice agent: a tool, two skills, a narrow surface.
_COACH = {
    "key": "practice-coach",
    "name": "연습 코치",
    "description": "연습 문제를 한 문항씩 내고 풀이를 점검합니다.",
    "system_prompt": "너는 연습 코치다. 한 문항씩 내고, 수리 답은 calculate 로 검산한다.",
    "guide": "문제를 요청하면 한 문항씩 냅니다.",
    "starters": ["수리 문제 하나 내 줘"],
    "kinds": ["chat"],
    "tools": ["calculate"],
    "skills": ["quiz-writer", "evidence"],
    "temperature": 0.3,
}


@pytest.fixture(autouse=True)
def _catalogue_agent(monkeypatch):
    monkeypatch.setattr(starter, "_AGENTS", [_COACH])
    use_text_skills(monkeypatch)


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
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                metadata.create_all,
                tables=[
                    metadata.tables[model.__tablename__]
                    for model in (User, Agent, Skill, StoredFile, Memory, ChatSession,
                                  Publication)
                ],
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _catalogue(db):
    admin = User(
        email="coach-admin@example.com", name="Admin", password_hash="x", role=UserRole.admin
    )
    learner = User(email="coach-learner@example.com", name="Learner", password_hash="x")
    db.add_all([admin, learner])
    await db.commit()
    await starter.seed_catalog(db, admin.id)
    await db.commit()
    agent = (await db.exec(select(Agent).where(Agent.catalog_key == "practice-coach"))).one()
    skill = (await db.exec(select(Skill).where(Skill.catalog_key == "quiz-writer"))).one()
    return admin, learner, agent, skill


@pytest.mark.asyncio
async def test_catalogue_seeding_and_installation_are_idempotent(db):
    admin, learner, original, original_skill = await _catalogue(db)
    assert await starter.seed_catalog(db, admin.id) == 0
    await db.commit()
    store = await ws.list_agents(learner, db)
    offered = next(row for row in store if row.id == original.id)
    assert offered.official and not offered.installed
    first = await ws.install_agent(original.id, learner, db)
    second = await ws.install_agent(original.id, learner, db)
    assert first.id == second.id
    installed = await db.get(Agent, first.id)
    copied_skill = await db.get(Skill, installed.skill_ids[0])
    assert installed.tools == ["calculate"]
    assert installed.model == ""
    assert installed.kinds == ["chat"]
    assert installed.temperature == 0.3
    assert installed.visibility is Visibility.private
    assert copied_skill.owner_id == learner.id
    assert copied_skill.origin_id == original_skill.id
    assert copied_skill.required_tools == []
    assert copied_skill.version == "1.0.0"
    assert copied_skill.kinds == ["chat", "slides"]
    assert len(installed.skill_ids) == 2
    evidence_skill = await db.get(Skill, installed.skill_ids[1])
    assert evidence_skill.catalog_key == "evidence"
    assert evidence_skill.required_tools == []
    assert evidence_skill.owner_id == learner.id
    assert (await ws.install_skill(original_skill.id, learner, db)).id == copied_skill.id
    agents = (await db.exec(select(Agent).where(Agent.catalog_key == "practice-coach"))).all()
    skills = (await db.exec(select(Skill).where(Skill.catalog_key == "quiz-writer"))).all()
    assert len(agents) == len(skills) == 2
    assert original.installs == original_skill.installs == 1
    store = await ws.list_agents(learner, db)
    assert next(row for row in store if row.id == original.id).installed


@pytest.mark.asyncio
async def test_agent_prompt_runs_without_activating_its_installed_skill(db):
    _, learner, original, _ = await _catalogue(db)
    installed = await ws.install_agent(original.id, learner, db)
    session = ChatSession(user_id=learner.id, agent_id=installed.id, kind=SessionKind.chat)
    db.add(session)
    await db.commit()
    context = await workspace_context.assemble(db, learner, session)
    messages = build_messages(
        SessionKind.chat,
        [{"role": "user", "content": "수리 영역 연습 문제 하나 내 줘"}],
        extra=context.trusted,
        untrusted_context=context.untrusted,
    )
    assert original.system_prompt in messages[0]["content"]
    assert context.applied_skills == ()
    assert not any(block.source.startswith("skill:") for block in context.blocks)
    assert await workspace_context.agent_settings(db, learner, session) == (
        None,
        ["calculate"],
        0.3,
    )

    selected = installed.skill_ids[0]
    context = await workspace_context.assemble(
        db,
        learner,
        session,
        activated_skill_ids=[selected],
        available_tool_names={"calculate"},
    )
    assert [skill.id for skill in context.applied_skills] == [selected]
    assert [skill.catalog_key for skill in context.applied_skills] == ["quiz-writer"]
    assert sum(block.source == f"skill:{selected}" for block in context.blocks) == 1
    citation = (await db.exec(select(Skill).where(Skill.catalog_key == "citation"))).one()
    citation_copy = await ws.install_skill(citation.id, learner, db)
    with pytest.raises(workspace_context.WorkspaceContextError, match="skill_not_allowed_by_agent"):
        await workspace_context.assemble(
            db, learner, session, activated_skill_ids=[citation_copy.id], available_tool_names=set()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
async def test_a_chat_only_agent_refuses_document_sessions(db, kind):
    _, learner, original, _ = await _catalogue(db)
    installed = await ws.install_agent(original.id, learner, db)
    session = ChatSession(user_id=learner.id, agent_id=installed.id, kind=kind)
    with pytest.raises(workspace_context.WorkspaceContextError, match="agent_kind_mismatch"):
        await workspace_context.assemble(db, learner, session)


@pytest.mark.asyncio
async def test_catalogue_refresh_preserves_edited_learner_copies(db):
    admin, learner, original, original_skill = await _catalogue(db)
    installed = await ws.install_agent(original.id, learner, db)
    copy = await db.get(Agent, installed.id)
    copied_skill = await db.get(Skill, copy.skill_ids[0])
    copy.system_prompt = "내가 수정한 지도 절차"
    copy.guide = "내가 수정한 안내"
    copied_skill.body = "내가 수정한 조건 검산 절차"
    original.system_prompt = "이전 카탈로그 프롬프트"
    original_skill.body = "관리자가 수정한 검산 절차"
    original_id, original_skill_id = original.id, original_skill.id
    learner_id = learner.id
    db.add_all([copy, copied_skill, original, original_skill])
    await db.commit()
    assert await starter.seed_catalog(db, admin.id) == 0
    await db.commit()
    db.expire_all()
    copy = await db.get(Agent, installed.id)
    copied_skill = await db.get(Skill, copy.skill_ids[0])
    assert copy.system_prompt == "내가 수정한 지도 절차"
    assert copy.guide == "내가 수정한 안내"
    assert copied_skill.body == "내가 수정한 조건 검산 절차"
    assert (await db.get(Skill, original_skill_id)).body == "관리자가 수정한 검산 절차"
    learner = await db.get(User, learner_id)
    assert (await ws.install_agent(original_id, learner, db)).id == copy.id


@pytest.mark.asyncio
async def test_a_skill_cannot_silently_run_without_its_required_tool(db):
    _, learner, original, _ = await _catalogue(db)
    installed = await ws.install_agent(original.id, learner, db)
    session = ChatSession(user_id=learner.id, agent_id=installed.id, kind=SessionKind.chat)
    verification = await db.get(Skill, installed.skill_ids[1])
    assert verification.catalog_key == "evidence"
    verification.required_tools = ["calculate"]
    db.add(verification)
    await db.commit()
    with pytest.raises(
        workspace_context.WorkspaceContextError, match="skill_tools_unavailable:calculate"
    ):
        await workspace_context.assemble(
            db,
            learner,
            session,
            activated_skill_ids=[verification.id],
            available_tool_names={"execute_code"},
        )


@pytest.mark.asyncio
async def test_refresh_never_widens_an_existing_tool_restriction(db):
    admin, learner, original, _ = await _catalogue(db)
    installed = await ws.install_agent(original.id, learner, db)
    copy = await db.get(Agent, installed.id)
    original.tools = []
    original.system_prompt = "Earlier shipped prompt"
    copy.tools = []
    copy.system_prompt = original.system_prompt
    db.add_all([original, copy])
    await db.commit()
    await starter.seed_catalog(db, admin.id)
    await db.commit()
    assert original.tools == copy.tools == []
    assert original.system_prompt == _COACH["system_prompt"]
    assert copy.system_prompt == original.system_prompt


@pytest.mark.asyncio
async def test_a_retired_catalogue_agent_leaves_the_owner_and_copies_and_own_agents_stay(
    db, monkeypatch
):
    """Dropping an agent from the catalogue deletes the owner's row on the next sync."""
    admin, learner, original, _ = await _catalogue(db)
    installed = await ws.install_agent(original.id, learner, db)
    learner_own = Agent(owner_id=learner.id, name="내 에이전트", slug="내-에이전트")
    admin_own = Agent(owner_id=admin.id, name="관리자 에이전트", slug="관리자-에이전트")
    db.add_all([learner_own, admin_own])
    await db.commit()
    original_id = original.id
    ids = {installed.id, learner_own.id, admin_own.id}

    monkeypatch.setattr(starter, "_AGENTS", [])
    await starter.sync_catalog(db, admin)
    await db.commit()
    db.expire_all()

    assert await db.get(Agent, original_id) is None
    remaining = (await db.exec(select(Agent))).all()
    assert {agent.id for agent in remaining} == ids
    # The learner's copy still runs on the skills it brought along.
    copy = await db.get(Agent, installed.id)
    assert copy.system_prompt == _COACH["system_prompt"]
    assert len(copy.skill_ids) == 2
    # Skills are still shipped and stay shared.
    quiz = (await db.exec(select(Skill).where(Skill.catalog_key == "quiz-writer"))).all()
    assert any(skill.visibility is Visibility.org for skill in quiz)


@pytest.mark.asyncio
async def test_with_no_agents_a_skill_made_private_stays_private_across_syncs(db, monkeypatch):
    monkeypatch.setattr(starter, "_AGENTS", [])
    admin = User(
        email="solo-admin@example.com", name="Admin", password_hash="x", role=UserRole.admin
    )
    db.add(admin)
    await db.commit()
    await starter.sync_catalog(db, admin)
    await db.commit()
    assert (await db.exec(select(Agent))).all() == []
    citation = (await db.exec(select(Skill).where(Skill.catalog_key == "citation"))).one()
    citation.visibility = Visibility.private
    db.add(citation)
    await db.commit()

    for _ in range(2):
        await starter.sync_catalog(db, admin)
        await db.commit()

    await db.refresh(citation)
    assert citation.visibility is Visibility.private
    others = (await db.exec(select(Skill).where(Skill.id != citation.id))).all()
    assert others and all(skill.visibility is Visibility.org for skill in others)
