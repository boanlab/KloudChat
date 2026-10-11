"""빠진 정보 질문 are data: a 서식 and an agent carry them, a person edits them in the agent form,
and the shipped agents ask theirs through the same path.

- The shipped lists moved out of `gaps.py` into `template.toml` / `agents/*.toml` unchanged.
- A person's agent with the same questions puts up the same card as the shipped agent.
- Any agent can ask: a 바로 답하기 agent with its own questions, a report on a 서식 with them.
- Detectors: built-in names, keywords, bounded patterns, the judge model (quoted evidence).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User
from app.models.workspace import Agent, Skill, StoredFile, UserPipeline
from app.routers import sessions
from app.routers import workspace as ws
from app.schemas.workspace import AgentIn, _base_questions, _inherited_questions
from app.services import check_skills, design_templates, gap_questions, starter

ANALYSIS = "월별 이용 통계야. 학기 중과 방학을 비교해 줘."
MEMO = "축제 부스 회의\n- 포스터 → 지민\n- 견적 → 도윤, 모레까지"


def test_the_shipped_lists_live_in_the_template_and_agent_files():
    assert [q["id"] for q in design_templates.get("doc-minutes").questions] == [
        "meeting_date", "attendees", "action_dues"]
    assert [q["id"] for q in design_templates.get("doc-gaejo-report").questions] == [
        "addressee", "reporter"]
    # A detector users can pick: 개조식 보고's questions are plain patterns, not code.
    assert {q["detector"]["type"] for q in design_templates.get("doc-gaejo-report").questions} \
        == {"regex"}
    assert [q["id"] for q in starter.agent_spec("data-analysis")["questions"]] == [
        "deadline_basis", "period_split", "denominator"]
    # 문서 작성 and 발표 자료 ship none; their 서식 or a person's own list supplies them.
    assert gap_questions.resolve(own=None, base_key="document") == []
    for template in design_templates.all_templates():
        # Every shipped question passes the check a person's does.
        assert gap_questions.clean(list(template.questions)) == list(template.questions)


def test_patterns_must_be_bounded():
    for template in design_templates.all_templates():
        for q in template.questions:
            if q["detector"]["type"] == "regex":
                assert gap_questions.regex_ok(q["detector"]["pattern"])
    assert gap_questions.regex_ok(r"참석\s{0,2}(?:자|인원)")
    for bad in (r"a.*b", r"(a+)+", r"(?:a{0,5}){0,5}", r"x{0,500}", r"(a)\1", "(", "a" * 301,
                r"\d+"):
        assert not gap_questions.regex_ok(bad), bad


def test_bad_questions_are_refused_with_a_code():
    good = {"id": "venue", "question": "장소는 어디인가요?",
            "detector": {"type": "keyword", "words": ["장소", "강당"]}}
    assert gap_questions.clean([good])[0]["label"] == "장소는 어디인가요?"
    for row, code in (
        ({**good, "detector": {"type": "regex", "pattern": ".*"}}, "gap_pattern_unsafe"),
        ({**good, "detector": {"type": "builtin", "name": "nope"}}, "gap_detector_unknown"),
        ({**good, "detector": {"type": "keyword", "words": []}}, "gap_keywords_empty"),
        ({**good, "question": ""}, "gap_question_empty"),
        ({**good, "id": "Bad id"}, "gap_id_invalid"),
    ):
        with pytest.raises(gap_questions.QuestionError) as caught:
            gap_questions.clean([row])
        assert caught.value.code == code
    with pytest.raises(gap_questions.QuestionError):
        gap_questions.clean([good, good])


def _card(questions, request, *, doc_format=None, agent_key=None, judge=("", "")):
    async def run():
        return await sessions._gaps(
            ("gap-check",), questions=questions, request=request, material="",
            first_draft=True, doc_format=doc_format, agent_key=agent_key,
            card={"plans_first": False, "may_ask": True, "files": [], "asked": []},
            judge=judge)
    return asyncio.run(run()).findings.questions


def test_a_person_s_agent_with_the_same_questions_gets_the_same_card():
    shipped = gap_questions.resolve(own=None, base_key="data-analysis")
    mine = gap_questions.resolve(own=[dict(q) for q in shipped], base_key=None)
    theirs = _card(shipped, ANALYSIS, agent_key="data-analysis")
    ours = _card(mine, ANALYSIS, agent_key=None)
    assert theirs and ours == theirs
    assert [q["fact"] for q in ours] == ["period_split"]
    # On a report: the 서식's questions, whichever agent writes in it.
    minutes = gap_questions.resolve(own=[], template_id="doc-minutes")
    assert _card(minutes, MEMO, doc_format="minutes") == _card(
        gap_questions.resolve(own=None, base_key="minutes", template_id="doc-minutes"), MEMO,
        doc_format="minutes", agent_key="minutes")


def test_a_direct_agent_asks_its_own_questions():
    own = gap_questions.clean([{
        "id": "venue", "label": "장소", "question": "행사 장소는 어디인가요?",
        "why": "안내문에 장소가 들어갑니다.", "placeholder": "[장소]",
        "detector": {"type": "keyword", "words": ["장소", "강당", "홀"], "when": ["안내"]}}])
    asked = gap_questions.resolve(own=own, base_key=None)
    card = _card(asked, "행사 안내문 써 줘. 일시는 10월 20일.")
    assert [(q["fact"], q["label"], q["placeholder"]) for q in card] == [
        ("venue", "장소", "[장소]")]
    # Answered by the material, or not applicable (`when`): nothing to ask.
    assert _card(asked, "행사 안내문 써 줘. 장소는 대강당.") == []
    assert _card(asked, "회의록 써 줘.") == []
    rule = check_skills.rule(("gap-check",), check_skills.gaps(
        ("gap-check",), questions=asked, request="행사 안내문 써 줘.", material=""))
    assert "장소 → [장소]" in rule


def test_the_judge_must_quote_the_material():
    asked = gap_questions.clean([
        {"id": "budget", "question": "예산 상한이 있나요?", "detector": {"type": "judge"}},
        {"id": "owner", "question": "누가 승인하나요?", "detector": {"type": "judge"}}])
    calls = []

    async def complete(model, messages, _key, _n):
        calls.append(model)
        return json.dumps({"answers": [
            {"id": "budget", "evidence": "예산은 300만 원 이내"},
            {"id": "owner", "evidence": "팀장이 승인"}]}), {"inputTokens": 3}

    usage: dict = {}
    got = asyncio.run(gap_questions.judged(
        asked, request="계획서 써 줘", material="예산은 300만 원 이내로 한다.",
        complete=complete, model="strict-local/judge", api_key="k", usage=usage))
    # The second quote is not in the material: that question is asked.
    assert got == {"budget": True, "owner": False} and calls == ["strict-local/judge"]
    assert usage["inputTokens"] == 3
    found = check_skills.gaps(("gap-check",), questions=asked, request="계획서 써 줘",
                              material="", judged=got)
    assert [f["fact"] for f in found] == ["owner"]
    # No model to judge with: nothing is asked on a guess.
    assert asyncio.run(gap_questions.judged(asked, request="", material="", complete=complete,
                                            model="", api_key="")) == {
        "budget": True, "owner": True}
    assert check_skills.gaps(("gap-check",), questions=asked, request="x", material="") == []


def test_the_card_stage_judges_with_the_turn_s_model(monkeypatch):
    from app.services import report

    asked = gap_questions.clean([{"id": "budget", "question": "예산 상한이 있나요?",
                                  "detector": {"type": "judge"}}])

    async def complete(model, messages, _key, _n):
        return json.dumps({"answers": [{"id": "budget", "evidence": ""}]}), {}

    monkeypatch.setattr(report, "_complete", complete)
    card = _card(asked, "계획서 써 줘", judge=("strict-local/judge", "k"))
    assert [q["fact"] for q in card] == ["budget"]


def test_the_gap_judge_model_stays_in_the_boundary():
    user = User(email="x@example.test", name="X", password_hash="h")
    models = [{"id": "cloud/big", "kinds": ["chat"], "strictLocal": False, "creditCost": 1},
              {"id": "strict-local/judge", "kinds": ["chat"], "strictLocal": True,
               "creditCost": 1}]
    assert sessions._gap_judge_model(user, models, models[0]) == "strict-local/judge"
    assert sessions._gap_judge_model(user, models[:1], models[0]) == ""


def test_the_gap_judge_is_the_planners_strict_local_entry():
    user = User(email="x@example.test", name="X", password_hash="h")
    models = [{"id": "local/big", "kinds": ["chat"], "strictLocal": False, "creditCost": 0},
              {"id": "strict-local/big", "kinds": ["chat"], "strictLocal": True, "creditCost": 0},
              {"id": "strict-local/planner", "kinds": ["chat"], "strictLocal": True,
               "creditCost": 0}]
    # The planner judges; by price and name alone the first strict-local model would.
    assert sessions._gap_judge_model(user, models, models[0], "local/planner") == (
        "strict-local/planner")
    # A planner with no strict-local entry, or none at all, leaves the first one.
    assert sessions._gap_judge_model(user, models, models[0], "cloud/x") == "strict-local/big"
    assert sessions._gap_judge_model(user, models, models[0], None) == "strict-local/big"
    # The turn's own strict-local model still comes first.
    assert sessions._gap_judge_model(user, models, models[1], "local/planner") == (
        "strict-local/big")


# ── the agent form ─────────────────────────────────────────────────────


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
                for m in (User, Agent, Skill, UserPipeline, StoredFile)])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def test_the_form_stores_checked_questions_and_shows_the_inherited(db):
    me = User(email="me@example.test", name="Me", password_hash="h")
    db.add(me)
    await db.commit()
    question = {"id": "venue", "label": "장소", "question": "장소는?",
                "detector": {"type": "keyword", "words": ["장소"]}}
    made = await ws.create_agent(AgentIn(
        name="행사 안내", kinds=["report"], surface="report", render_template_id="doc-minutes",
        gap_questions=[question]), me, db)
    assert [q["id"] for q in made.gap_questions] == ["venue"]
    assert [q["id"] for q in made.inherited_questions] == [
        "meeting_date", "attendees", "action_dues"]
    with pytest.raises(HTTPException) as caught:
        await ws.patch_agent(made.id, AgentIn(name="행사 안내", gap_questions=[
            {**question, "detector": {"type": "regex", "pattern": "(a+)+"}}]), me, db)
    assert caught.value.status_code == 422 and caught.value.detail == "gap_pattern_unsafe"
    row = await db.get(Agent, made.id)
    asked = gap_questions.resolve(own=row.gap_questions, base_key=None,
                                  template_id=row.render_template_id)
    assert [q["id"] for q in asked] == ["meeting_date", "attendees", "action_dues", "venue"]


def test_a_shipped_agent_shows_its_questions_as_its_row_holds_them():
    spec = starter.agent_spec("data-analysis")
    row = Agent(owner_id="o", name="데이터 분석", catalog_key="data-analysis",
                gap_questions=[dict(q) for q in spec["questions"]], kinds=["chat"])
    # Its own are on the row; nothing is inherited (it has no 서식).
    assert _inherited_questions(row) == [] and _base_questions(row) == []
    # An agent on that base that stores none asks the base's: the form starts from them.
    copy = Agent(owner_id="o", name="데이터 분석", base_key="data-analysis", kinds=["chat"])
    assert [q["id"] for q in _base_questions(copy)] == [
        "deadline_basis", "period_split", "denominator"]
    for name in gap_questions.builtin_names():
        assert gap_questions.DETECTOR_LABELS[name]


def test_readings_follow_the_built_in_detectors_named():
    minutes = gap_questions.for_format("minutes")
    got = gap_questions.readings(minutes, "2026-10-07 회의", "", dt.date(2026, 10, 8))
    assert set(got) == {"meeting_date"}
    assert gap_questions.readings(gap_questions.for_format("gaejo"), "x", "", None) == {}
    proposal = gap_questions.for_format("client_proposal")
    assert {"period", "staff", "priced", "vat"} <= set(
        gap_questions.readings(proposal, "x", "y", None))
