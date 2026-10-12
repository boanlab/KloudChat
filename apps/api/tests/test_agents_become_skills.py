"""D21: the shipped agents give way to skills; people build agents from skills.

Each withdrawn agent's abilities are an admin-shared skill package: its instructions, the
템플릿 it wrote into, the checks its 묶음 attached, its 빠진 정보 질문 and its 진행 설정. A skill
brings those to any turn it is on, and an agent's 「켜 둘 스킬」 are on in every turn of its
conversations (and frozen into a published bot's prompt)."""

from __future__ import annotations

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.user import User, UserRole
from app.models.workspace import Agent, Connector, Skill, Visibility
from app.schemas.workspace import ConnectorOut
from app.services import (
    design_templates,
    gap_questions,
    publishing,
    skill_packages,
    skill_specs,
    skill_turn,
    starter,
    workspace_context,
)
from app.services.tools import blurbs

#: withdrawn agent → the skill that carries what it did
SKILL_OF = {
    "minutes": "minutes-writing",
    "comparison-report": "option-comparison",
    "gaejo-report": "gaejo-briefing",
    "data-analysis": "data-tabulation",
    "document": "document-drafting",
    "slides": "deck-writing",
    "regulation-guide": "regulation-answer",
}


def test_every_withdrawn_agent_has_its_skill():
    assert starter.withdrawn_agents() == set(SKILL_OF)
    assert not starter.offered_agents(drafts=True)


@pytest.mark.parametrize(("agent", "key"), sorted(SKILL_OF.items()))
def test_the_skill_carries_what_the_agent_did(agent, key):
    spec = starter.agent_spec(agent)
    package = skill_packages.package_for(key)
    made = skill_specs.of_package(package)
    assert package.listed and not package.draft
    # its instructions (the regulation one also reads attached files with no shelf)
    if agent == "regulation-guide":
        assert spec["system_prompt"].split("1.")[0] in package.body
        assert "search_knowledge" in package.body
    else:
        # what the agent's own surface reads (a `[채팅에서]` part is for chat)
        surface = SessionKind(spec["surface"])
        assert workspace_context.surface_body(package.body, surface) == spec["system_prompt"]
    # where it writes: the agent's surface first, and its template
    assert package.kinds[0] == spec["surface"]
    assert made.template == (spec.get("template") or "")
    # the checks its 묶음 compiled to (the template's own come with the template)
    template = design_templates.get(made.template) if made.template else None
    held = {*made.checks, *(template.check_skills if template else ())}
    assert set(spec["checks"]) - {"answer-hold", "contact-note", "pii-mask"} <= held
    assert set(made.check_options) == set(spec["check_options"])
    # its own 빠진 정보 질문 and 진행 설정
    assert [q["id"] for q in made.questions] == [q["id"] for q in spec.get("questions") or []]
    assert made.process == (spec.get("process") or {})


@pytest.mark.parametrize("key", sorted(skill_packages.packages()))
def test_a_packages_document_fields_name_real_things(key):
    package = skill_packages.package_for(key)
    made = skill_specs.of_package(package)
    if made.template:
        template = design_templates.get(made.template)
        assert template is not None
        assert {"document": "report", "deck": "slides"}[template.kind] in package.kinds
    assert set(made.checks) <= set(skill_specs.check_keys())
    assert len(made.questions) == len(package.questions)
    assert gap_questions.clean(list(made.questions)) == list(made.questions)


def test_the_connector_companions_need_their_tools():
    youtube = skill_packages.package_for("youtube-summary")
    research = skill_packages.package_for("deep-research")
    assert youtube.required_tools == ("mcp__youtube__transcript",)
    assert research.required_tools == ("mcp__deep-research__quick_research",)
    # 시간 is folded into 날짜 계산: today comes from the connector, never a guess
    assert "mcp__time__get_current_time" in skill_packages.package_for("date-calc").body


def test_a_persons_skill_sets_the_same_fields_and_unknown_ones_are_refused():
    fields, errors = skill_specs.validate({
        "template": "doc-minutes", "checks": ["gap-check", "grounding-check"],
        "check_options": ["titles"], "process": {"search": False, "draws": "when-asked"},
        "questions": [{"id": "venue", "question": "어디서 했나요?",
                       "detector": {"type": "keyword", "words": ["장소"]}}]})
    assert not errors
    assert fields["template"] == "doc-minutes" and fields["checks"] == ["gap-check",
                                                                       "grounding-check"]
    assert fields["process"] == {"search": False, "draws": "when-asked"}
    assert fields["questions"][0]["id"] == "venue"
    spec = skill_specs.of_row(Skill(owner_id="u", name="내 회의록", server_fields=fields))
    assert spec.template == "doc-minutes" and spec.check_options == ("titles",)

    _, errors = skill_specs.validate({"template": "nope", "checks": ["my-own", "skill:abc"],
                                      "check_options": ["loose"], "process": {"draws": "x"}})
    assert {e.code for e in errors} == {"skill_template_unknown", "skill_check_unknown",
                                        "skill_check_option_unknown", "process_draws"}
    assert skill_specs.CHECK_OPTION_NAMES == tuple(
        __import__("app.services.check_skills", fromlist=["x"]).CHECK_OPTIONS)


def test_what_several_skills_bring_adds_up_and_the_first_wins():
    first = skill_specs.Spec(key="a", template="doc-minutes", checks=("gap-check",),
                             process={"search": False}, questions=({"id": "q1"},))
    second = skill_specs.Spec(key="b", template="doc-comparison",
                              checks=("gap-check", "grounding-check"), check_options=("titles",),
                              process={"search": True, "draws": "when-asked"},
                              questions=({"id": "q1"}, {"id": "q2"}))
    got = skill_turn.merge([first, second])
    assert got.template == "doc-minutes"
    assert got.checks == ("gap-check", "grounding-check")
    assert got.process == {"search": False, "draws": "when-asked"}
    assert [q["id"] for q in got.questions] == ["q1", "q2"]
    # under the agent's own
    assert got.checks_with(["generic-language-killer"]) == [
        "generic-language-killer", "gap-check", "grounding-check"]
    assert got.process_with({"draws": "auto"}) == {"search": False, "draws": "auto"}
    assert got.options_with({}) == {"titles": True}
    assert skill_turn.Brought().process_with(None) is None


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
                metadata.tables[model.__tablename__]
                for model in (User, Agent, Skill, ChatSession)])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _people(db):
    admin = User(email="skl-admin@example.com", name="관리자", password_hash="x",
                 role=UserRole.admin)
    member = User(email="skl-member@example.com", name="회원", password_hash="x")
    other = User(email="skl-other@example.com", name="다른 회원", password_hash="x")
    db.add_all([admin, member, other])
    await db.commit()
    return admin, member, other


@pytest.mark.asyncio
async def test_an_agent_built_from_skills_keeps_them_on_and_they_bring_their_template(db):
    admin, member, other = await _people(db)
    shipped = skill_packages.package_for("minutes-writing")
    official = Skill(owner_id=admin.id, name=shipped.name, body=shipped.body,
                     catalog_key=shipped.key, kinds=["report"], visibility=Visibility.org,
                     server_fields=skill_specs.package_fields(shipped))
    copy = Skill(owner_id=member.id, name=shipped.name, body=shipped.body, kinds=["report"],
                 catalog_key=shipped.key, origin_id=official.id)
    plain = Skill(owner_id=member.id, name="결론 먼저", body="결론을 맨 앞에 쓴다.",
                  kinds=["chat", "report"])
    off = Skill(owner_id=member.id, name="꺼 둔 것", body="x", enabled=False)
    db.add_all([official, copy, plain, off])
    await db.commit()
    agent = Agent(owner_id=member.id, name="우리 팀 회의록", kinds=["report"],
                  surface="report", visibility=Visibility.org,
                  active_skill_ids=[copy.id, plain.id, off.id])
    db.add(agent)
    await db.commit()

    rows = await workspace_context.agent_skill_rows(db, member.id, agent, "report")
    assert [r.id for r in rows] == [copy.id, plain.id]
    # another member running the shared agent gets the same skills
    assert [r.id for r in await workspace_context.agent_skill_rows(
        db, other.id, agent, "report")] == [copy.id, plain.id]
    # a chat turn takes only what works there
    assert [r.id for r in await workspace_context.agent_skill_rows(
        db, member.id, agent, "chat")] == [plain.id]

    session = ChatSession(user_id=member.id, kind=SessionKind.report, agent_id=agent.id)
    db.add(session)
    await db.commit()
    found = await skill_turn.brought(db, member, session, agent, [])
    assert found.template == "doc-minutes"
    assert {"gap-check", "grounding-check", "generic-language-killer"} <= set(found.checks)
    assert await skill_turn.template_for(db, member, session, found) == "doc-minutes"
    # the person's pick (or the agent's own template) wins
    session.render_template_id = "doc-result-report"
    assert await skill_turn.template_for(db, member, session, found) is None


@pytest.mark.asyncio
async def test_a_bot_answers_with_the_skills_its_agent_is_built_from(db):
    _, member, _ = await _people(db)
    rule = Skill(owner_id=member.id, name="규정 근거 답변", body="근거 조항을 [n]으로 단다.",
                 kinds=["chat"], server_fields={"checks": ["grounding-check", "pii-mask"]})
    db.add(rule)
    await db.commit()
    agent = Agent(owner_id=member.id, name="학사 안내", system_prompt="학사 규정을 안내한다.",
                  kinds=["chat"], active_skill_ids=[rule.id], check_skills=["answer-hold"])
    db.add(agent)
    await db.commit()
    prompt, checks = await publishing.built_from(db, agent)
    assert prompt.startswith("학사 규정을 안내한다.")
    assert "# 스킬 — 규정 근거 답변\n근거 조항을 [n]으로 단다." in prompt
    assert checks == ["answer-hold", "grounding-check", "pii-mask"]


def test_a_connector_asks_only_for_what_the_person_enters():
    youtube = Connector(owner_id="u", name="YouTube 전사", slug="youtube",
                        env={"WHISPER_URL": "${TOOLS_STT_URL}"})
    assert ConnectorOut.of(youtube).env_keys == []
    github = Connector(owner_id="u", name="GitHub", slug="github",
                       env={"GITHUB_TOKEN": "ghp_x", "USER": "{{USER_ID}}"})
    assert ConnectorOut.of(github).env_keys == ["GITHUB_TOKEN"]
    # tool panels read a Korean line, not the model's docstring
    assert blurbs.blurb("youtube", "transcript", "Returns the full transcript…").startswith(
        "YouTube 영상의 자막")
    assert blurbs.blurb("other", "lookup", "Look things up.\nMore.") == "Look things up."


@pytest.mark.asyncio
async def test_a_report_keeps_its_skills_for_the_approval_turn(db):
    """A skill picked on a report's first turn stays on: the plan's approval carries no picks."""
    from app.routers import sessions as sessions_router

    assert {SessionKind.report, SessionKind.slides} <= sessions_router._STANDING_KINDS
    _, member, _ = await _people(db)
    minutes = Skill(owner_id=member.id, name="회의록 작성", body="x", kinds=["report"],
                    server_fields={"template": "doc-minutes", "checks": ["gap-check"]})
    chat_only = Skill(owner_id=member.id, name="자료 집계", body="y", kinds=["chat"])
    db.add_all([minutes, chat_only])
    await db.commit()
    session = ChatSession(user_id=member.id, kind=SessionKind.report,
                          skill_ids=[minutes.id, chat_only.id])
    db.add(session)
    await db.commit()
    kept = await sessions_router._standing_skill_ids(db, member, session, None)
    assert kept == [minutes.id] and session.skill_ids == [minutes.id]
    found = await skill_turn.brought(db, member, session, None, kept)
    assert await skill_turn.template_for(db, member, session, found) == "doc-minutes"


def test_answers_from_attached_regulations_are_not_held():
    """The hold reads the files attached to the conversation, not only the shelf's hits."""
    from app.services import skill_scripts

    hold = skill_scripts.module("answer-hold", "hold")
    answer = "휴학은 학기 개시일 전까지 학사지원팀에 신청서를 내야 합니다 [1]."
    material = ("수강신청 안내를 알려 줘\n# 이번 요청에 첨부된 파일\n"
                "아래는 이미 읽어 둔 자료 본문입니다.\n"
                "## 학칙.md\n제12조 휴학은 학기 개시일 전까지 학사지원팀에 신청서를 내야 한다.\n"
                "# 파일 처리 결과\n- 학칙.md")
    assert hold.check(answer, material, [], {"surface": "chat"}) == []
    # the question alone is no evidence
    assert hold.check(answer, "휴학은 언제까지 신청서를 내야 하나요", [], {"surface": "chat"})
    unrelated = material.replace("휴학은 학기 개시일 전까지 학사지원팀에 신청서를 내야 한다",
                                 "장학금은 성적 상위 10퍼센트 이내에게 지급한다")
    assert hold.check(answer, unrelated, [], {"surface": "chat"})
