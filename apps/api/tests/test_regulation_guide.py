"""규정·업무 안내: answers cite the regulation by number with its document, article, page and
시행일; what the material does not hold is held back and sent to the 담당 부서; personal
details are masked — each by a part a person attaches the same way (the 「공개 전 점검」 묶음 and
the 담당 부서·연락처 field), so an agent built in the form is the official one."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.user import User, UserRole
from app.models.workspace import Agent
from app.pipeline import registry as pipeline_registry
from app.routers import sessions
from app.routers import user_pipelines as up
from app.routers import workspace as ws
from app.schemas.workspace import AgentIn
from app.services import agent, bundles, check_skills, knowledge, skill_scripts, starter
from app.services.tools.base import ToolContext
from app.services.tools.builtin import knowledge_tool
from app.services.workspace_context import _agent_block

_CONTACT = "학사지원팀 02-760-1234, haksa-desk@example.ac.kr"

_LONG = "\n".join(
    [
        "학칙",
        "[시행 2024. 3. 1.]",
        "부칙 <2025. 2. 28.> 이 학칙은 2025년 3월 1일부터 시행한다.",
        "[페이지 1]",
        "제1장 총칙",
        "제1조(목적) 이 학칙은 대학의 학사 운영에 관한 사항을 정한다. " + "학사 운영 " * 120,
        "[페이지 2]",
        "제12조(휴학) ① 휴학하려는 학생은 학기 개시일 전까지 휴학원을 제출하여야 한다. "
        "② 일반휴학 기간은 통산 3년을 넘을 수 없다. " + "휴학 절차 " * 60,
        "[페이지 3]",
        "제13조(복학) 휴학한 학생은 휴학 기간이 끝나는 학기의 개시 30일 전까지 복학원을 낸다. "
        + "복학 절차 " * 200,
    ]
)


# ── citations ──────────────────────────────────────────────────────────


def test_a_passage_names_its_document_article_page_and_date():
    passages = knowledge.search([("학칙.pdf", _LONG, None)], "휴학 기간은 통산 몇 년")
    best = passages[0]
    assert best.section == "제12조(휴학)" and best.page == 2
    # The latest 시행일 the document states, not the first date in it.
    assert best.dated == "시행 2025. 3. 1."
    assert best.label == "학칙.pdf · 제12조(휴학) · 2쪽 · 시행 2025. 3. 1."
    assert knowledge.render([best]).startswith("[1] 자료: 학칙.pdf · 제12조(휴학) · 2쪽")


def test_a_small_regulation_comes_back_an_article_at_a_time():
    text = "복무 규정\n제1조(목적) 목적이다.\n제2조(정의) 정의다.\n제2조의2 (적용) 적용이다."
    passages, ranked = knowledge.gather([("복무규정.hwpx", text, None)], "정의")
    assert ranked is False
    assert [p.section for p in passages] == ["제1조(목적)", "제2조(정의)", "제2조의2(적용)"]
    assert "제2조(정의) 정의다." in passages[1].text


@pytest.mark.parametrize(("text", "want"), [
    ("[시행 2024. 3. 1.] … [시행 2025. 9. 1.]", "시행 2025. 9. 1."),
    ("2023.2.28. 개정", "개정 2023. 2. 28."),
    ("이 규정은 2026년 1월 1일부터 시행한다.", "시행 2026. 1. 1."),
    ("날짜 없는 지침", ""),
])
def test_the_date_in_force_is_read_from_the_document(text, want):
    assert knowledge.dated(text) == want


def test_material_passages_are_numbered_across_the_turn_and_cited_without_links():
    sources: list[str] = []
    call = {"name": "search_knowledge", "arguments": "{}"}
    first = agent._number_sources(
        "[1] 자료: 학칙 · 제12조(휴학)\n본문\n\n[2] 자료: 학칙 · 제13조(복학)\n본문", sources, call)
    second = agent._number_sources(
        "[1] 자료: 장학 규정 · 제5조(지급)\n본문\n\n[2] 자료: 학칙 · 제12조(휴학)\n본문",
        sources, call)
    assert "[1] 자료: 학칙 · 제12조(휴학)" in first and "[2] 자료: 학칙 · 제13조(복학)" in first
    # The same article keeps its number; a new one takes the next.
    assert "[3] 자료: 장학 규정 · 제5조(지급)" in second
    assert "[1] 자료: 학칙 · 제12조(휴학)" in second
    linked, cited = agent._link_citations("휴학은 개시일 전까지 [1]. 장학금은 [3].", sources)
    assert linked == "휴학은 개시일 전까지 [1]. 장학금은 [3]." and cited == [1, 3]


async def test_the_answer_lists_the_cited_articles_at_the_end(monkeypatch):
    tool = knowledge_tool([("학칙.pdf", _LONG, None)])
    replies = [
        ["data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "c0", "function": {"name": "search_knowledge",
                                                 "arguments": json.dumps({"query": "휴학 기간"})}}]
        }}]}), "data: [DONE]"],
        ["data: " + json.dumps({"choices": [{"delta": {"content":
                                                       "일반휴학은 통산 3년까지입니다 [2]."}}]}),
         "data: [DONE]"],
    ]

    class _Response:
        status_code = 200

        def __init__(self, lines):
            self.lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *_args, json):
            return _Response(replies.pop(0))

    async def client(*_a, **_k):
        return _Client()

    monkeypatch.setattr(agent, "_client", client)
    text = ""
    ctx = ToolContext(user_id="u", session_id="s", api_key="k")
    async for event in agent.run_turn("m", [{"role": "user", "content": "q"}], [tool], ctx):
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            text = text.replace(event["text"], "", 1)
    # A small regulation comes back an article at a time: 제12조 is the second passage.
    assert "통산 3년까지입니다 [2]." in text and "[[2]]" not in text
    assert "### 출처\n- [2] 학칙.pdf · 제12조(휴학) · 2쪽 · 시행 2025. 3. 1." in text


# ── the 공개 전 점검 checks ────────────────────────────────────────────


_PASSAGE = ("[1] 자료: 학칙 · 제12조(휴학)\n"
            "제12조(휴학) 휴학하려는 학생은 학기 개시일 전까지 휴학원을 제출하여야 한다.")


def _options(**extra) -> dict:
    return check_skills.options_for(None, {"contact": _CONTACT}, surface="chat") | extra


def test_with_nothing_found_the_answer_is_held_and_names_the_contact():
    hold = skill_scripts.entry("answer-hold")
    answer = "주차 요금은 한 시간에 1,000원입니다. 정기권은 3만 원입니다."
    found = hold.check(answer, "", ["자료 1건을 찾아봤지만 '주차' 와 겹치는 대목이 없습니다."],
                       _options())
    held = hold.fix(answer, found)
    assert held == ("제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다.\n\n"
                    f"담당 부서·연락처: {_CONTACT}")
    assert hold.check(held, "", [], _options()) == []
    # Without a contact on the agent.
    bare = check_skills.options_for(None, {}, surface="chat")
    assert hold.fix(answer, hold.check(answer, "", [], bare)).endswith("담당 부서에 문의해 주세요.")


def test_an_answer_resting_on_what_was_found_is_kept():
    hold = skill_scripts.entry("answer-hold")
    answer = "휴학하려는 학생은 학기 개시일 전까지 휴학원을 제출해야 합니다 [1]."
    assert hold.check(answer, "", [_PASSAGE], _options()) == []
    # Found, but the answer says something else entirely.
    unrelated = "주차장은 정문 옆에 있고 요금은 시간당 천 원으로 정해져 있습니다."
    (found,) = hold.check(unrelated, "", [_PASSAGE], _options())
    assert found["why"] == "찾은 자료와 맞는 문장이 없음"
    # A follow-up resting on an earlier cited answer is not held.
    earlier = "휴학원은 학기 개시일 전까지 냅니다 [1].\n\n### 출처\n- [1] 학칙 · 제12조(휴학)"
    follow = "휴학원은 학기 개시일 전까지 내야 하니 그 전에 준비하세요."
    assert hold.check(follow, "", [earlier], _options()) == []
    # A report section is not a chat answer.
    assert hold.check(unrelated, "", [], {"surface": "report"}) == []


def test_a_partial_answer_gets_the_contact_as_written_once():
    note = skill_scripts.entry("contact-note")
    answer = ("휴학 기한은 학기 개시일 전까지입니다 [1]. 수수료는 자료에 나와 있지 않습니다.\n\n"
              "### 출처\n- [1] 학칙")
    found = note.check(answer, "", [], _options())
    fixed = note.fix(answer, found)
    assert f"나와 있지 않습니다.\n\n담당 부서·연락처: {_CONTACT}\n\n### 출처" in fixed
    assert note.check(fixed, "", [], _options()) == []
    assert note.check("휴학 기한은 학기 개시일 전까지입니다 [1].", "", [], _options()) == []


def test_personal_details_are_masked_and_the_contact_kept():
    mask = skill_scripts.entry("pii-mask")
    # On the server it is the instance's own detector.
    assert mask._governance is not None
    answer = ("담당 조교 010-1234-5678 또는 hong.gildong@gmail.com 으로 연락하세요.\n"
              f"담당 부서·연락처: {_CONTACT}\n대표번호 02-760-1000, help@example.ac.kr")
    found = mask.check(answer, "", [], _options())
    masked = mask.fix(answer, found)
    assert "010-1234-5678" not in masked and "hong.gildong@gmail.com" not in masked
    assert "[전화번호]" in masked and "[이메일]" in masked
    assert _CONTACT in masked and "02-760-1000" in masked and "help@example.ac.kr" in masked
    assert mask.summary(found) == "개인정보 2개 가림"
    # The sandbox's own patterns agree on the same text.
    real, mask._governance = mask._governance, None
    try:
        assert mask.fix(answer, mask.check(answer, "", [], _options())) == masked
    finally:
        mask._governance = real


def test_the_bundle_runs_in_order_and_says_what_it_did():
    keys = ("answer-hold", "contact-note", "pii-mask")
    out = check_skills.ground(keys, "주차 요금은 1,000원이고 문의는 010-1111-2222입니다.",
                              material="", computed=[], options=_options())
    assert out.text.startswith("제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다.")
    assert "010-1111-2222" not in out.text and _CONTACT in out.text
    assert out.traced_only is False and out.summary.startswith("근거 없는 답 보류")


def test_the_contact_reaches_the_model_word_for_word():
    row = Agent(owner_id="u", name="안내", system_prompt="규정으로 답한다.", contact=_CONTACT)
    block = _agent_block(row, SessionKind.chat)
    assert "# 담당 부서·연락처" in block and block.rstrip().endswith(_CONTACT)
    assert _agent_block(Agent(owner_id="u", name="x", system_prompt="지시"), SessionKind.chat) \
        == "# 역할\n지시"


# ── the official agent and one made in the form ────────────────────────


def test_the_official_agent_is_a_chat_agent_on_its_material_with_the_publishing_checks():
    spec = starter.agent_spec("regulation-guide")
    assert spec["draft"] is True and "regulation-guide" in starter.draft_agents()
    assert spec["tools"] == ["search_knowledge", "calculate"] and spec["skills"] == []
    assert spec["checks"] == ["grounding-check", "answer-hold", "contact-note", "pii-mask"]
    assert [b["bundle"] for b in spec["bundles"]] == ["review", "publish-check"]
    prompt = spec["system_prompt"]
    assert "제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다." in prompt
    assert "담당 부서에 문의해 주세요." in prompt and "시행일" in prompt
    # No claim the copy may not make.
    for text in (spec["description"], spec["guide"], *spec["starters"]):
        assert "지어내지" not in text and "보장" not in text and "우리만" not in text


@pytest.fixture
async def local():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    names = ["users", "agents", "sessions", "messages", "files", "skills", "user_pipelines",
             "design_systems", "audit_events", "pipeline_runs", "artifacts",
             "artifact_versions", "connectors", "connector_tools", "connector_credentials",
             "connector_catalog"]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[metadata.tables[n] for n in names])

    def make():
        return AsyncSession(engine, expire_on_commit=False)

    try:
        yield make
    finally:
        await engine.dispose()


async def test_an_agent_made_in_the_form_is_the_official_one(local):
    """The same fields in the agent form and the same 묶음 ticked under 진행 과정 give the row the
    official agent holds, and its turns the same context, tools, checks and pipeline."""
    spec = starter.agent_spec("regulation-guide")
    async with local() as db:
        admin = User(email="a@example.com", name="관리자", password_hash="x", role=UserRole.admin)
        db.add(admin)
        await db.commit()
        official = Agent(owner_id=admin.id, name=spec["name"], slug="official",
                         catalog_key="regulation-guide", kinds=spec["kinds"],
                         surface=spec["surface"], system_prompt=spec["system_prompt"],
                         tools=spec["tools"], skill_ids=[], check_skills=spec["checks"],
                         check_options=spec["check_options"] or None, bundles=spec["bundles"],
                         temperature=spec["temperature"], contact=_CONTACT)
        db.add(official)
        await db.commit()
        made = await ws.create_agent(AgentIn(
            name="우리 학과 규정 안내", system_prompt=spec["system_prompt"], kinds=["chat"],
            surface="chat", tools=["search_knowledge", "calculate"], skill_ids=[],
            temperature=spec["temperature"], contact=_CONTACT), admin, db)
        await up.put_process(made.id, up.ProcessIn(mode="direct", bundles=[
            {"bundle": "review", "items": {}}, {"bundle": "publish-check", "items": {}}]),
            admin, db)
        mine = await db.get(Agent, made.id)
        fields = ("system_prompt", "tools", "skill_ids", "kinds", "surface", "check_skills",
                  "temperature", "contact", "render_template_id", "doc_format", "pipeline_id")
        assert {f: getattr(mine, f) for f in fields} == {f: getattr(official, f) for f in fields}
        assert (mine.check_options or None) == (official.check_options or None)
        assert [b["bundle"] for b in mine.bundles] == [b["bundle"] for b in official.bundles]
        # The same finishing pipeline and the same checks attached at run time.
        keys = [await sessions._agent_catalog_key(db, a) for a in (mine, official)]
        assert pipeline_registry.for_chat(keys[0]).name == pipeline_registry.for_chat(
            keys[1]).name == "chat"
        assert check_skills.attached(agent_checks=mine.check_skills) == check_skills.attached(
            agent_checks=official.check_skills)
        # The model reads the same role and contact.
        assert _agent_block(mine, SessionKind.chat) == _agent_block(official, SessionKind.chat)
        # The bundles compile to the same on any surface it could be put on.
        compiled = bundles.compile("chat", mine.bundles, bundles.shipped())
        assert compiled.check_skills == official.check_skills and compiled.steps is None
        assert ChatSession(user_id=admin.id).kind is SessionKind.chat
