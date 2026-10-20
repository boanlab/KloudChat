"""「항상 적용」 skills (`Agent.active_skill_ids`): part of the skill list, within a budget."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession
from app.models.user import User
from app.models.workspace import (
    Agent,
    DesignSystem,
    Memory,
    Skill,
    StoredFile,
    UserPipeline,
    Visibility,
)
from app.routers import sessions
from app.routers import workspace as ws
from app.schemas.workspace import AgentIn
from app.services import workspace_context


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
            await conn.run_sync(metadata.create_all, tables=[
                metadata.tables[m.__tablename__]
                for m in (User, Agent, Skill, DesignSystem, StoredFile, Memory, ChatSession,
                          UserPipeline)])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _member(db, email: str = "pin@example.com") -> User:
    user = User(email=email, name="회원", password_hash="x")
    db.add(user)
    await db.commit()
    return user


def _skill(user: User, name: str, size: int = 40, **kw) -> Skill:
    body = "가" * size
    return Skill(owner_id=user.id, name=name, when_to_use=f"{name}이 필요할 때", body=body,
                 estimated_tokens=max(1, round(size / 3.5)), **kw)


@pytest.mark.asyncio
async def test_a_chosen_skill_list_takes_in_the_always_on_skills(db):
    user = await _member(db)
    mail, tone = _skill(user, "메일 문체"), _skill(user, "빠진 정보 질문")
    db.add_all([mail, tone])
    await db.commit()

    out = await ws.create_agent(AgentIn(name="후속 메일", skill_ids=[mail.id],
                                        active_skill_ids=[tone.id]), user, db)
    assert out.skill_ids == [mail.id, tone.id] and out.active_skill_ids == [tone.id]
    # 「기본값 따르기」 stays null
    open_list = await ws.create_agent(AgentIn(name="열린 목록", active_skill_ids=[tone.id]),
                                      user, db)
    assert open_list.skill_ids is None
    # turning a needed-when skill on joins the list; turning it off leaves it there
    patched = await ws.patch_agent(out.id, AgentIn(name="후속 메일",
                                                   active_skill_ids=[mail.id, tone.id]), user, db)
    assert patched.skill_ids == [mail.id, tone.id]
    patched = await ws.patch_agent(out.id, AgentIn(name="후속 메일", active_skill_ids=[]),
                                   user, db)
    assert patched.skill_ids == [mail.id, tone.id] and patched.active_skill_ids == []
    # an emptied list with a kept-on skill keeps that one
    patched = await ws.patch_agent(out.id, AgentIn(name="후속 메일", skill_ids=[],
                                                   active_skill_ids=[tone.id]), user, db)
    assert patched.skill_ids == [tone.id]


@pytest.mark.asyncio
async def test_always_on_skills_past_the_budget_are_refused_and_trimmed_at_run_time(db):
    user = await _member(db)
    per = workspace_context.AGENT_SKILL_TOKEN_BUDGET // 2 + 100
    big, bigger, small = (_skill(user, "긴 절차", int(per * 3.5)),
                          _skill(user, "더 긴 절차", int(per * 3.5)), _skill(user, "짧은 규칙"))
    db.add_all([big, bigger, small])
    await db.commit()

    with pytest.raises(HTTPException) as refused:
        await ws.create_agent(AgentIn(name="넘침", active_skill_ids=[big.id, bigger.id]),
                              user, db)
    assert refused.value.detail == "active_skills_too_long"
    made = await ws.create_agent(AgentIn(name="맞춤", active_skill_ids=[big.id, small.id]),
                                 user, db)

    # a skill edited longer later: the run keeps the order and skips what no longer fits
    agent = await db.get(Agent, made.id)
    agent.active_skill_ids = [big.id, bigger.id, small.id]
    db.add(agent)
    await db.commit()
    rows = await workspace_context.agent_skill_rows(db, user.id, agent, "chat")
    assert [r.id for r in rows] == [big.id, small.id]
    # an over-budget set already saved may still be saved as it is, or trimmed
    await ws.patch_agent(made.id, AgentIn(name="맞춤", active_skill_ids=[big.id, bigger.id]),
                         user, db)
    await ws.patch_agent(made.id, AgentIn(name="맞춤", active_skill_ids=[big.id]), user, db)


@pytest.mark.asyncio
async def test_installed_and_allowed_skills_not_kept_on_are_offered_through_use_skill(db):
    user = await _member(db)
    admin = await _member(db, "pin-admin@example.com")
    shared = _skill(admin, "공유 절차", visibility=Visibility.org)
    db.add(shared)
    await db.commit()
    installed = await ws._install_skill(db, user, shared)
    kept, other = _skill(user, "항상 쓰는 것"), _skill(user, "목록 밖")
    db.add_all([kept, other])
    await db.commit()
    agent = Agent(owner_id=user.id, name="도우미", skill_ids=[installed.id, kept.id],
                  active_skill_ids=[kept.id])
    db.add(agent)
    await db.commit()

    shelf = await sessions._skill_shelf(db, user, agent=agent,
                                        exclude=set(agent.active_skill_ids))
    assert [name for name, _, _ in shelf] == ["공유 절차"]


@pytest.mark.asyncio
async def test_a_copy_of_a_shared_agent_carries_its_always_on_skills(db):
    owner = await _member(db)
    other = await _member(db, "pin-other@example.com")
    rule = _skill(owner, "근거 먼저", visibility=Visibility.org)
    db.add(rule)
    await db.commit()
    made = await ws.create_agent(AgentIn(name="공유 도우미", visibility=Visibility.org,
                                         skill_ids=[], active_skill_ids=[rule.id]), owner, db)
    assert made.skill_ids == [rule.id]

    copy = await ws.install_agent(made.id, other, db)
    mine = await db.get(Skill, copy.active_skill_ids[0])
    assert mine.owner_id == other.id and mine.origin_id == rule.id
    assert copy.skill_ids == copy.active_skill_ids
