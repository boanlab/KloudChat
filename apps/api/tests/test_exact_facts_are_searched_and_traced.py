"""Changing or exact facts searched first; advice answers traced."""

from __future__ import annotations

import json

import pytest
from fastapi.responses import StreamingResponse
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message, Role
from app.models.user import User
from app.pipeline import chat_stages
from app.pipeline.check_stages import TRACE, AnswerGrounding
from app.pipeline.stage import Context
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import check_skills, exact_facts, freshness, query_terms
from app.services.tools.base import Tool

DOCTOR_TURNS = [
    "가상 의사가 되어 줘. 내가 증상을 말하면 진단과 치료 계획을 알려 줘. "
    "진단과 치료 계획만 답하고 다른 건 쓰지 마. 설명도 쓰지 마. "
    "첫 요청: 며칠째 두통과 어지럼증이 있어.",
    "36살 남자고, 요즘 야근이 많아서 하루 5시간쯤 자. "
    "일어설 때 어지러운 게 제일 심하고, 오늘 아침엔 한쪽 팔이 좀 저렸어.",
]
COUNSELLOR_TURNS = [
    "정신건강 상담가가 되어 줘. "
    "감정, 스트레스, 불안 같은 정신건강 문제를 다루는 법을 찾는 사람에게 "
    "인지행동치료, 명상, 마음챙김 등 치료적 방법을 써서 실천할 수 있는 전략을 세워 줘. "
    "첫 요청: 우울 증상을 다스리는 데 도움을 받고 싶어.",
]
INVESTING_TURNS = [
    "금융시장 경험이 많은 사람의 조언이 필요해. "
    "인플레이션, 기대수익률, 장기 주가 흐름 같은 걸 고려해서 섹터를 이해하게 돕고, "
    "내 필요와 관심에 맞춰 가장 안전한 선택지를 알려 줘. "
    "첫 질문: 지금 단기적으로 돈을 굴리기 가장 좋은 방법은 뭐야?",
    "1년 안에 전세 보증금에 보탤 3천만 원이야. 원금 손실은 싫어.",
]
TRANSLATOR_TURNS = [
    "영어 번역가이자 맞춤법 교정·문장 개선가가 되어 줘. "
    "내가 어떤 언어로 말하든 언어를 알아내서 번역하고, 영어로 고치고 다듬은 문장으로 답해 줘. "
    "고친 문장만 답하고, 다른 건 쓰지 마. 첫 문장: "
    '"istanbulu cok seviyom burada olmak cok guzel"',
    "요즘 회사 일이 너무 많아서 주말에 아무것도 안 하고 그냥 잤어",
]
EXCEL_FORMULA_REQUEST = (
    "엑셀 수식 생성기가 되어 줘. 지역별·월별 매출 합계를 계산하는 수식이 필요해.\n\n"
    "데이터: '판매' 시트에 A열 날짜, B열 지역, C열 담당자, D열 금액이 2행부터 약 5,000행까지 "
    "있어.\n\n조건: 반품은 금액이 음수로 들어오는데 이건 빼고 합계를 내고 싶어. 연도는 2026년만. "
    "회사에 엑셀 2016을 쓰는 사람이 있어서 거기서도 돌아가야 해."
)
LEGAL_ADVICE_TURNS = [
    "내 법률 자문가가 되어 줘. 법적 상황을 설명하면 어떻게 대처할지 조언해 줘. 조언만 답하고, 다른 "
    "건 쓰지 마. 설명도 쓰지 마. 첫 요청: 교통사고가 났는데 어떻게 해야 할지 모르겠어.",
    "방금 사거리에서 신호 대기 중에 뒤차가 내 차를 박았어. 다친 데는 없는 것 같은데 목이 좀 "
    "뻐근해. 상대방은 보험 처리 말고 현금으로 합의하자고 해.",
]
ACCOUNTANT_TURNS = [
    "회계사가 되어 줘. 재정을 관리할 창의적인 방법을 생각해 줘. "
    "예산, 투자 전략, 위험 관리를 고려해서 재무 계획을 세우고, "
    "필요하면 이익을 늘리는 데 도움이 될 세법·규정 조언도 해 줘. "
    "첫 요청: 비용 절감과 장기 투자에 초점을 둔 소기업 재무 계획을 세워 줘.",
    "직원 6명짜리 동네 베이커리 카페야. 연 매출은 약 4억 원, 임대료가 월 600만 원, 원재료비는 "
    "매출의 35%쯤 돼. 2년 안에 2호점을 내고 싶어.",
]
SALES_CALL_TURNS = [
    "영업사원이 되어 줘. 나한테 뭔가를 팔아 봐. 실제보다 더 값어치 있어 보이게 해서 내가 사도록 "
    "설득해. 이제 네가 나한테 전화를 걸었다고 치고 내가 무슨 일로 전화했는지 물어볼게. 여보세요, "
    "무슨 일로 전화하셨어요?",
    "음… 그거 얼마예요?",
    "생각해 볼게요. 지금 꼭 필요한지 잘 모르겠어서요.",
]


# ── what forces the first search ────────────────────────────────────────


@pytest.mark.parametrize(("turns", "reason"), [
    (INVESTING_TURNS[:1], "financial"),
    # the follow-up gives only the amount: the thread decides
    (INVESTING_TURNS, "financial"),
    (DOCTOR_TURNS[:1], "medical"),
    (DOCTOR_TURNS, "medical"),
    (COUNSELLOR_TURNS, "medical"),
    (LEGAL_ADVICE_TURNS, "legal"),
    (ACCOUNTANT_TURNS, "tax"),
    (["자살예방 상담전화 번호가 뭐야?"], "medical"),
    (["여권 재발급 문의처 전화번호 알려 줘"], "contact"),
    (["예금자보호 한도가 얼마야?"], "financial"),
    (["청년 월세 지원금 신청 자격 알려 줘"], "rules"),
    (["I want to visit only museums near Beyoğlu in Istanbul."], "schedule"),
    (["I need help uncovering facts about the early 20th century labor strikes in London.",
      "Which primary sources should I look at first, and where are they held?"], "organization"),
])
def test_facts_that_change_or_must_be_exact_force_the_search(turns, reason):
    assert exact_facts.search_reason(turns[-1], turns[:-1]) == reason


@pytest.mark.parametrize("turns", [
    [EXCEL_FORMULA_REQUEST],                                # a spreadsheet formula
    TRANSLATOR_TURNS[:1], TRANSLATOR_TURNS,                 # a translator, and its next sentence
    # a pretend sales call: its price is the role-play's
    SALES_CALL_TURNS[:1], SALES_CALL_TURNS[:2], SALES_CALL_TURNS,
    ["확률이 뭔지 개념부터 설명해 줘. 주사위 두 개를 던질 때 합이 7일 확률은?"],
    ["사랑에 관한 시를 써 줘."],
    ["Explain this revenue SQL query to a non-engineer: SELECT SUM(amount) FROM orders"],
    ["Write a persuasive essay on reducing plastic waste."],
    ["점심시간에 빨리 만들 수 있는 가볍고 든든한 요리를 알려 줘."],
    ["백엔드 개발자로 6년 일했는데 커리어 고민이 있어."],
])
def test_writing_translation_code_role_play_and_learning_are_left_alone(turns):
    assert exact_facts.search_reason(turns[-1], turns[:-1]) is None
    assert exact_facts.advice_domain(turns[-1], turns[:-1]) is None


@pytest.mark.parametrize(("turns", "domain"), [
    (LEGAL_ADVICE_TURNS, "legal"), (ACCOUNTANT_TURNS, "tax"), (INVESTING_TURNS, "financial"),
    (DOCTOR_TURNS, "medical"), (COUNSELLOR_TURNS, "medical"),
])
def test_advice_domains(turns, domain):
    assert exact_facts.advice_domain(turns[-1], turns[:-1]) == domain


def test_a_contract_draft_is_drafting_not_a_lookup():
    ask = "Draft a one-page design partner agreement covering IP, confidentiality and data use."
    assert exact_facts.search_reason(ask) is None
    assert exact_facts.advice_domain(ask) is None


# ── the route forces it for every model ─────────────────────────────────


def _search_tool() -> Tool:
    async def unused(_arguments):
        pytest.fail("the route ran the search itself")

    return Tool(name="web_search", description="search", parameters={"type": "object"},
                run=unused, label="search", read_only=True)


async def _route(monkeypatch, content: str, *, toggle="auto", earlier: list[str] = ()):
    user = User(email="synthetic@example.test", password_hash="hash", name="Synthetic")
    model = {**_external_model("synthetic/qwen"), "supportsTools": True}
    session = ChatSession(user_id=user.id, model=model["id"])
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    captured: dict = {}

    async def tools(*_args, **_kwargs):
        return [_search_tool()]

    async def history(*_args, **_kwargs):
        rows = []
        for text in earlier:
            rows += [Message(session_id=session.id, role=Role.user, content=text),
                     Message(session_id=session.id, role=Role.assistant,
                             content="네, 말씀해 주세요.")]
        return rows

    async def run(**kwargs):
        captured.update(kwargs)
        yield sessions.chat_service.sse({"type": "done"})

    async def key(*_args):
        return None

    async def credentials(*_args):
        return "synthetic-origin", "synthetic-noncredential"

    class Db(_NoWriteDb):
        def is_modified(self, _row):
            return False

    monkeypatch.setattr(sessions, "build_tools", tools)
    monkeypatch.setattr(sessions, "_history", history)
    monkeypatch.setattr(sessions, "_run_turn", run)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    response = await sessions.send_message(
        session.id, SendMessage(content=content, web_search=toggle), _request(), user, Db())
    assert isinstance(response, StreamingResponse)
    _ = [json.loads(c.removeprefix("data: ").strip()) async for c in response.body_iterator]
    return captured


@pytest.mark.asyncio
async def test_an_advice_question_searches_first_and_is_traced(monkeypatch):
    captured = await _route(monkeypatch, INVESTING_TURNS[0])
    assert captured["force_tool"] == "web_search"
    assert captured["advice"] == "financial"
    assert TRACE in captured["check_keys"]
    # the writer is told to source its figures and keep the practical guidance
    assert captured["freshness_request"] is None


@pytest.mark.asyncio
async def test_a_follow_up_is_searched_by_the_model_with_the_thread_in_view(monkeypatch):
    captured = await _route(monkeypatch, INVESTING_TURNS[1], earlier=INVESTING_TURNS[:1])
    assert captured["force_tool"] == "web_search"
    # the amount alone is no query: the model writes it from the conversation
    assert captured["preset_call"] is None
    assert captured["advice"] == "financial"


@pytest.mark.asyncio
@pytest.mark.parametrize("content",
                         [EXCEL_FORMULA_REQUEST, TRANSLATOR_TURNS[0], SALES_CALL_TURNS[0]])
async def test_other_turns_are_not_forced_or_traced(monkeypatch, content):
    captured = await _route(monkeypatch, content)
    assert captured["force_tool"] is None
    assert captured["advice"] == ""
    assert TRACE not in captured["check_keys"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "toggle"), [
    (INVESTING_TURNS[0], False),
    (INVESTING_TURNS[0] + " 검색하지 마.", "auto"),
])
async def test_search_off_or_declined_is_kept(monkeypatch, content, toggle):
    captured = await _route(monkeypatch, content, toggle=toggle)
    assert captured["force_tool"] is None
    # the answer is still traced: grounding needs no network
    assert captured["advice"] == "financial"


# ── the advice answer's concrete claims are traced ───────────────────────


def _advice(answer: str, *, request: str, domain: str, searched: list[str] = ()):
    return chat_stages.bundle_for(
        answer=answer, request=request, model={"id": "m"}, actual_model=None, api_key="k",
        session_id="s", today=None, first_message=request,
        messages=[{"role": "user", "content": request}], material=request,
        check_keys=(TRACE,), check_material="", tool_outputs=[], judge="", kept_steps=[],
        usage={}, evidence=list(searched), advice=domain,
    )


async def _ground(bundle) -> list[dict]:
    stage = AnswerGrounding()
    assert not stage.skip(bundle)
    return [event async for event in stage.run(bundle, Context())]


@pytest.mark.asyncio
async def test_an_invented_criminal_consequence_is_marked():
    bundle = _advice(
        "1. 사고 현장과 차량 사진을 찍어 두세요.\n"
        "2. 과실비율이 높으면 형사처벌을 받을 수 있습니다.\n"
        "3. 목이 뻐근하면 오늘 병원 진료를 받고 진단서를 받아 보험사에 제출하세요.",
        request=LEGAL_ADVICE_TURNS[1], domain="legal")
    events = await _ground(bundle)
    answer = bundle.document.answer
    assert "형사처벌을 받을 수 있습니다 (확인 필요)." in answer
    # the practical steps stay, documents to hand in included
    assert "진단서를 받아 보험사에 제출하세요." in answer
    assert events[-1]["label"].startswith("근거 확인(법률): 근거 없는 값 1개")


@pytest.mark.asyncio
async def test_a_consequence_the_search_results_state_stands():
    searched = ["교통사고처리특례법: 12대 중과실 사고는 보험 가입과 관계없이 형사처벌 대상이다."]
    bundle = _advice("신호위반 같은 12대 중과실이면 형사처벌 대상이 될 수 있습니다.",
                     request=LEGAL_ADVICE_TURNS[1], domain="legal", searched=searched)
    events = await _ground(bundle)
    assert "(확인 필요)" not in bundle.document.answer
    assert events[-1]["label"] == "근거 확인(법률): 확인 필요한 값 없음"


@pytest.mark.asyncio
async def test_an_old_hotline_is_marked_and_the_searched_one_stands():
    searched = ["자살예방 상담전화 109 (2024년 1월 1일부터 통합 운영)"]
    bundle = _advice(
        "혼자 견디지 마세요. 위급하면 자살예방상담전화 1393이나 109로 전화하세요.",
        request=COUNSELLOR_TURNS[0], domain="medical", searched=searched)
    await _ground(bundle)
    answer = bundle.document.answer
    assert "1393(확인 필요)" in answer
    assert "109로 전화하세요" in answer


@pytest.mark.asyncio
async def test_invented_tax_thresholds_are_marked_once_below_the_answer():
    bundle = _advice(
        "연 매출 4억 원이면 일반과세자입니다. "
        "간이과세는 연 매출 8,000만원 미만일 때 적용되며 세율은 1.5~4%입니다. "
        "원재료비는 1억 4천만 원(35%)입니다. "
        "소득세 최고세율 45%는 매출 10억 원 이상에 적용됩니다.\n\n"
        "2주 안에 세무사와 상담해 보세요.",
        request=ACCOUNTANT_TURNS[1], domain="tax")
    events = await _ground(bundle)
    answer = bundle.document.answer
    # the person's own figures and their arithmetic stand
    assert "4억 원" in answer and "1억 4천만 원(35%)" in answer
    # four unsupported values: listed once, not spread through the text
    assert answer.count("(확인 필요)") == 0
    assert answer.rstrip().splitlines()[-1].startswith("확인 필요: ")
    assert "「8,000만원」" in answer or "8,000만원" in answer.splitlines()[-1]
    # guidance on when to see a professional is not a value to source
    assert "2주 안에 세무사와 상담해 보세요." in answer
    assert "근거 없는 값 4개" in events[-1]["label"]


@pytest.mark.asyncio
async def test_an_old_deposit_limit_is_marked_and_a_searched_rate_stands():
    searched = ["예금자보호한도 1억원으로 상향(2025년 9월 1일 시행). 파킹통장 금리 연 2.5%."]
    bundle = _advice(
        "원금 손실이 싫다면 예금자보호가 되는 상품이 맞습니다. 보호 한도는 1인당 5천만원입니다. "
        "파킹통장은 연 2.5% 수준입니다.",
        request=INVESTING_TURNS[1], domain="financial", searched=searched)
    await _ground(bundle)
    answer = bundle.document.answer
    assert "5천만원(확인 필요)" in answer
    assert "연 2.5% 수준" in answer and "2.5%(확인 필요)" not in answer
    assert "3천만" not in answer or "3천만 원(확인 필요)" not in answer


@pytest.mark.asyncio
async def test_a_dose_is_marked_but_durations_are_guidance():
    bundle = _advice(
        "수면을 하루 7시간 이상으로 늘리세요. 타이레놀 500mg을 드세요. 2주 이상 계속되면 "
        "신경과 진료를 받으세요. 팔 저림이 다시 오면 바로 응급실로 가세요.",
        request=DOCTOR_TURNS[1], domain="medical")
    await _ground(bundle)
    answer = bundle.document.answer
    assert "500mg(확인 필요)" in answer
    assert "7시간 이상" in answer and "7시간(확인 필요)" not in answer
    assert "2주 이상 계속되면" in answer


@pytest.mark.asyncio
async def test_an_earlier_answer_is_no_evidence_for_advice():
    bundle = chat_stages.bundle_for(
        answer="위급하면 1393으로 전화하세요.", request=COUNSELLOR_TURNS[0], model={"id": "m"},
        actual_model=None, api_key="k", session_id="s", today=None,
        first_message=COUNSELLOR_TURNS[0],
        messages=[{"role": "user", "content": COUNSELLOR_TURNS[0]},
                  {"role": "assistant", "content": "자살예방상담전화는 1393입니다."}],
        material=COUNSELLOR_TURNS[0], check_keys=(TRACE,), check_material="", tool_outputs=[],
        judge="", kept_steps=[], usage={}, advice="medical",
    )
    await _ground(bundle)
    assert "1393(확인 필요)" in bundle.document.answer


def test_the_trace_marks_nothing_twice():
    text = "보호 한도는 5천만원(확인 필요)입니다. 1393(확인 필요)으로 전화하세요."
    found = check_skills.entry(TRACE).check(text, "", [], {"advice": True})
    assert found == []


# ── the writer's rule and the planner ───────────────────────────────────


def test_the_writer_is_told_to_source_advice_figures():
    messages = freshness.with_answer_policy(
        [{"role": "user", "content": LEGAL_ADVICE_TURNS[1]}], {"id": "m"}, current_fact=False,
        advice="legal")
    system = messages[0]["content"]
    assert "This turn asks for legal guidance" in system
    assert "Never invent a tax rule" in system
    plain = freshness.with_answer_policy([{"role": "user", "content": EXCEL_FORMULA_REQUEST}],
                                         {"id": "m"})
    assert "This turn asks for" not in plain[0]["content"]


@pytest.mark.parametrize(("query", "request_text", "expected"), [
    ("API 요금 2026", "OpenRouter 요금제가 지금 얼마야?", "OpenRouter API 요금 2026"),
    ("OpenRouter pricing", "OpenRouter 요금제가 지금 얼마야?", "OpenRouter pricing"),
    ("예금자보호 한도", "예금자보호 한도가 얼마야?", "예금자보호 한도"),
])
def test_the_planned_query_keeps_the_requests_named_things(query, request_text, expected):
    assert query_terms.keep_entities(query, request_text) == expected
