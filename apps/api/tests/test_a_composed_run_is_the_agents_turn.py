"""직접 구성 runs with the same skills, 서식 settings, scripts and judges as an agent turn."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User
from app.models.workspace import Agent, Skill
from app.pipeline import compose
from app.pipeline.bundle import Bundle, Request
from app.pipeline.stage import Context, Wait
from app.routers import user_pipelines as up
from app.services import check_skills


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
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__] for m in (User, Agent, Skill)])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


async def test_an_agents_own_check_skill_and_settings_reach_its_step(db):
    me = User(email="me@example.com", name="나", password_hash="x")
    db.add(me)
    await db.commit()
    skill = Skill(owner_id=me.id, name="내 근거 확인", body="",
                  server_fields={"stage": "post", "entry": "grounding-check/trace"})
    db.add(skill)
    await db.commit()
    agent = Agent(owner_id=me.id, slug="a", name="경력 정리", kinds=["report"], surface="report",
                  render_template_id="doc-career-history",
                  check_skills=[f"skill:{skill.id}"],
                  process_settings={"rules": ["경력마다 기간을 적는다."]})
    db.add(agent)
    await db.commit()
    info = (await up.agents_for(db, me))[agent.id]
    assert f"skill:{skill.id}" in info.checks
    assert info.specs[f"skill:{skill.id}"]["entry"] == "grounding-check/trace"
    # The 서식's strictness (a career history traces titles) and the agent's settings.
    assert info.check_options == {"titles": True}
    assert info.process == {"rules": ["경력마다 기간을 적는다."]}
    # Over the wire and back (a waiting run is stored), the step still holds them.
    again = compose.AgentInfo.of_wire(json.loads(json.dumps(info.wire())))
    assert again.skill_specs()[f"skill:{skill.id}"].stage == "post"
    args = compose._sub_args(Bundle(request=Request(text="경력", model="m", api_key="k")),
                             again, "경력")
    assert args["process"] == {"rules": ["경력마다 기간을 적는다."]}
    assert check_skills.attached(agent_checks=list(again.checks),
                                 specs=args["skill_specs"]) == tuple(again.checks)


async def test_a_composed_card_asks_what_the_judge_finds_missing(monkeypatch):
    from app.services import report

    questions = [{"id": "audience", "label": "보고 대상", "question": "누구에게 올리나요?",
                  "why": "머리에 들어갑니다.", "placeholder": "[보고 대상]",
                  "detector": {"type": "judge"}}]
    calls = []

    async def complete(model, messages, api_key, max_tokens):
        calls.append(model)
        return json.dumps({"answers": [{"id": "audience", "evidence": ""}]}), {}

    monkeypatch.setattr(report, "_complete", complete)
    step = compose.GapsStep(0, {"id": "s1", "kind": "human", "ref": "gaps", "settings": {}},
                            "확인 질문", questions=questions)
    bundle = Bundle(request=Request(text="보고서 써 줘", model="writer", api_key="k",
                                    outline_model="judge", turn={}))
    with pytest.raises(Wait):
        async for _ in step.run(bundle, Context()):
            pass
    assert calls == ["judge"]
    assert [g["fact"] for g in bundle.findings.gaps] == ["audience"]
    # Answered on the card, the run goes on with the answer as material; no second reading.
    bundle.request.turn["resume"] = step.name
    bundle.answers = {"gap:audience": "시설과장님"}
    async for _ in step.run(bundle, Context()):
        pass
    assert calls == ["judge"]
    assert any("시설과장님" in p.text for p in bundle.material.parts)
