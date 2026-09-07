"""NCS catalogue integration; the reviewed exercises are inputs for separate model QA."""

from __future__ import annotations

import json
from collections import Counter
from fractions import Fraction
from itertools import permutations
from pathlib import Path

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.user import User, UserRole
from app.models.workspace import Agent, Memory, Skill, StoredFile, Visibility
from app.routers import workspace as ws
from app.services import starter, workspace_context
from app.services.context import build_messages


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
                    for model in (User, Agent, Skill, StoredFile, Memory, ChatSession)
                ],
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _catalogue(db):
    admin = User(
        email="ncs-admin@example.com", name="Admin", password_hash="x", role=UserRole.admin
    )
    learner = User(email="ncs-learner@example.com", name="Learner", password_hash="x")
    db.add_all([admin, learner])
    await db.commit()
    await starter.seed_catalog(db, admin.id)
    await db.commit()
    agent = (await db.exec(select(Agent).where(Agent.catalog_key == "ncs-coach"))).one()
    skill = (await db.exec(select(Skill).where(Skill.catalog_key == "ncs-reasoning"))).one()
    return admin, learner, agent, skill


@pytest.mark.asyncio
async def test_ncs_catalogue_and_installation_are_idempotent(db):
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
    assert installed.tools == []
    assert installed.model == ""
    assert installed.kinds == ["chat"]
    assert installed.temperature == 0.3
    assert installed.visibility is Visibility.private
    assert copied_skill.owner_id == learner.id
    assert copied_skill.origin_id == original_skill.id
    assert copied_skill.required_tools == []
    assert copied_skill.kinds == ["chat"]
    assert (await ws.install_skill(original_skill.id, learner, db)).id == copied_skill.id
    agents = (await db.exec(select(Agent).where(Agent.catalog_key == "ncs-coach"))).all()
    skills = (await db.exec(select(Skill).where(Skill.catalog_key == "ncs-reasoning"))).all()
    assert len(agents) == len(skills) == 2
    assert original.installs == original_skill.installs == 1
    store = await ws.list_agents(learner, db)
    assert next(row for row in store if row.id == original.id).installed


@pytest.mark.asyncio
async def test_ncs_core_prompt_runs_without_activating_its_installed_skill(db):
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
    assert await workspace_context.agent_settings(db, learner, session) == (None, [], 0.3)

    selected = installed.skill_ids[0]
    context = await workspace_context.assemble(
        db, learner, session, activated_skill_ids=[selected], available_tool_names=set()
    )
    assert [skill.id for skill in context.applied_skills] == [selected]
    assert [skill.catalog_key for skill in context.applied_skills] == ["ncs-reasoning"]
    assert sum(block.source == f"skill:{selected}" for block in context.blocks) == 1
    quiz = (await db.exec(select(Skill).where(Skill.catalog_key == "quiz-writer"))).one()
    quiz_copy = await ws.install_skill(quiz.id, learner, db)
    with pytest.raises(workspace_context.WorkspaceContextError, match="skill_not_allowed_by_agent"):
        await workspace_context.assemble(
            db, learner, session, activated_skill_ids=[quiz_copy.id], available_tool_names=set()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
async def test_ncs_agent_refuses_document_sessions(db, kind):
    _, learner, original, _ = await _catalogue(db)
    installed = await ws.install_agent(original.id, learner, db)
    session = ChatSession(user_id=learner.id, agent_id=installed.id, kind=kind)
    with pytest.raises(workspace_context.WorkspaceContextError, match="agent_kind_mismatch"):
        await workspace_context.assemble(db, learner, session)


@pytest.mark.asyncio
async def test_ncs_catalogue_refresh_preserves_edited_learner_copies(db):
    admin, learner, original, original_skill = await _catalogue(db)
    installed = await ws.install_agent(original.id, learner, db)
    copy = await db.get(Agent, installed.id)
    copied_skill = await db.get(Skill, copy.skill_ids[0])
    copy.system_prompt = "내가 수정한 NCS 지도 절차"
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
    assert copy.system_prompt == "내가 수정한 NCS 지도 절차"
    assert copy.guide == "내가 수정한 안내"
    assert copied_skill.body == "내가 수정한 조건 검산 절차"
    assert (await db.get(Skill, original_skill_id)).body == "관리자가 수정한 검산 절차"
    learner = await db.get(User, learner_id)
    assert (await ws.install_agent(original_id, learner, db)).id == copy.id


_QUESTIONS = json.loads((Path(__file__).parent / "fixtures/ncs_coach_questions.json").read_text())


def test_review_set_has_four_questions_per_area_and_one_identified_answer():
    assert len({row["case_id"] for row in _QUESTIONS}) == 12
    assert Counter(row["area"] for row in _QUESTIONS) == {
        "의사소통": 4, "수리": 4, "문제해결": 4
    }
    assert Counter(row["answer"] for row in _QUESTIONS) == {1: 3, 2: 3, 3: 3, 4: 3}
    for row in _QUESTIONS:
        assert len(set(row["choices"])) == 4
        assert 1 <= row["answer"] <= 4
        assert row["rationale"]


@pytest.mark.parametrize(
    ("case_id", "expected"),
    [
        ("ncs-numeracy-01", Fraction(100 - 80, 80) * 100),
        ("ncs-numeracy-02", Fraction(12 * 70 + 8 * 95, 12 + 8)),
        ("ncs-numeracy-03", 1 / (Fraction(1, 6) + Fraction(1, 3))),
        ("ncs-numeracy-04", 120000 * Fraction(80, 100) * Fraction(110, 100)),
    ],
)
def test_review_set_numeracy_answers_are_independently_calculated(case_id, expected):
    row = next(row for row in _QUESTIONS if row["case_id"] == case_id)
    assert Fraction(row["expected_value"]) == expected
    answers = [
        Fraction(choice.removesuffix(row["unit"]).replace(",", "")) for choice in row["choices"]
    ]
    assert answers.count(expected) == 1
    assert answers[row["answer"] - 1] == expected


@pytest.mark.parametrize("case_id", ["ncs-problem-solving-01", "ncs-problem-solving-02"])
def test_review_set_ordering_questions_have_one_feasible_solution(case_id):
    row = next(row for row in _QUESTIONS if row["case_id"] == case_id)
    if case_id.endswith("01"):
        feasible = [
            order for order in permutations("ABCD")
            if order.index("D") < order.index("A") < order.index("B") < order.index("C")
        ]
        choices = [tuple(choice.split(" → ")) for choice in row["choices"]]
    else:
        feasible = [
            order for order in permutations("ABC")
            if order[0] != "A" and order.index("C") == order.index("B") + 1
        ]
        choices = [tuple(choice.split(", ")) for choice in row["choices"]]
    assert len(feasible) == 1
    assert choices[row["answer"] - 1] == feasible[0]
    assert sum(choice in feasible for choice in choices) == 1


def test_review_set_vendor_constraints_include_the_boundary_values():
    row = next(row for row in _QUESTIONS if row["case_id"] == "ncs-problem-solving-04")
    candidates = [(95, 5, 99.5), (105, 3, 99.9), (100, 4, 99), (90, 3, 98.9)]
    feasible = [
        index for index, (budget, days, reliability) in enumerate(candidates, start=1)
        if budget <= 100 and days <= 4 and reliability >= 99
    ]
    assert feasible == [row["answer"]]
