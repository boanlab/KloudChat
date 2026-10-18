"""Answer scaffolding that steps back where it would over-constrain the answer."""

from __future__ import annotations

import copy

import pytest

from app.routers.sessions import _reads_pasted_material
from app.services import agent, answer_plan, freshness, grounding, report
from app.services import outline as outline_rules
from app.services.tools.base import Tool, ToolContext, ToolResult

P14 = ("가상 의사가 되어 줘. 내가 증상을 말하면 진단과 치료 계획을 알려 줘. 진단과 치료 계획만 "
       "답하고 다른 건 쓰지 마. 설명도 쓰지 마. 첫 요청: 며칠째 두통과 어지럼증이 있어.")
P14_2 = "36살 남자고, 요즘 야근이 많아서 하루 5시간쯤 자. 일어설 때 어지러운 게 제일 심하고, 오늘 아침엔 한쪽 팔이 좀 저렸어."  # noqa: E501
P48 = ("내 법률 자문가가 되어 줘. 법적 상황을 설명하면 어떻게 대처할지 조언해 줘. 조언만 답하고, "
       "다른 건 쓰지 마. 설명도 쓰지 마. 첫 요청: 교통사고가 났는데 어떻게 해야 할지 모르겠어.")
P16 = ("정신건강 상담가가 되어 줘. 감정, 스트레스, 불안 같은 정신건강 문제를 다루는 법을 찾는 "
       "사람에게 인지행동치료, 명상, 마음챙김 등 치료적 방법을 써서 실천할 수 있는 전략을 세워 "
       "줘. 첫 요청: 우울 증상을 다스리는 데 도움을 받고 싶어.")
P16_2 = "몇 달째 아침에 일어나기가 힘들고, 좋아하던 것도 재미가 없어. 회사는 겨우 다니고 있어. 상담은 받아 본 적 없어."  # noqa: E501
P56 = ("회계사가 되어 줘. 재정을 관리할 창의적인 방법을 생각해 줘. 예산, 투자 전략, 위험 관리를 "
       "고려해서 재무 계획을 세우고, 필요하면 이익을 늘리는 데 도움이 될 세법·규정 조언도 해 줘. "
       "첫 요청: 비용 절감과 장기 투자에 초점을 둔 소기업 재무 계획을 세워 줘.")
P36 = ("교정자가 되어 줘. 내가 글을 주면 맞춤법, 문법, 문장부호 오류를 검토하고, 다 본 뒤에 필요한 "
       "수정이나 글을 낫게 할 제안을 해 줘.\n\n안녕하세요. 지난주 회의에서 말씀드린 일정 변경건에 "
       "대해 다시한번 안내 드립니다. 프로젝트 마감일이 이번달 말에서 다음달 15일로 변경되였습니다. "
       "각 팀은 변경된 일정에 맞춰 업무 계획을 수정하시고, 금요일 까지 저에게 회신 부탁드립니다. "
       "궁금하신점은 언제던지 연락 주세요. 감사합니다.")
P38 = ("전문 이메일 작가가 되어 줘. 상황과 목적에 맞게 이메일을 쓰고, 알맞은 인사와 맺음말을 넣어 "
       "줘.\n- 어조: 격식 있게\n- 언어: 영어\n- 길이: 짧게\n\n상황: 싱가포르 거래처 담당자에게 다음 "  # noqa: E501
       "주 화상회의를 잡고 싶어. 3분기 납품 일정이 2주 늦어진 걸 사과하고 새 일정을 협의하려는 거야.")  # noqa: E501
P38_2 = "좀 더 친근하게 바꿔 줘. 그 담당자랑은 몇 년 같이 일해서 꽤 편한 사이야."
P38_FORMAL = (
    "Subject: Request for a Video Meeting Regarding Q3 Delivery Schedule\n\nDear [Name],\n\n"
    "I hope this message finds you well. I am writing to sincerely apologise for the two-week "
    "delay in our Q3 deliveries and to propose a video meeting next week to agree on a revised "
    "schedule.\n\nKind regards,\n[Your Name]")
P38_FRIENDLY = (
    "Subject: Q3 Delay – Let’s Catch Up Next Week\n\nHi [Name],\n\nI’m really sorry about the "
    "two-week slip on our Q3 deliveries. I know that’s frustrating, and I appreciate your "
    "patience while we sort it out.\n\nCould we hop on a quick video call next week to go over "
    "a new timeline?\n\nThanks so much,\n[Your Name]")
A01_T1 = (
    "아래 메모로 회의록 작성해 줘. 결정 사항과 할 일(담당·기한)이 한눈에 보이게.\n\n[메모] 10/8(목) "  # noqa: E501
    "14시 본관 3층 소회의실, 교육혁신팀 주간회의\n참석: 박정민 팀장, 이수진 차장, 김도윤 대리, "
    "최하늘 주임 (정현우 과장 출장으로 불참)\n1. 2학기 AI 활용 교수법 워크숍 — 신청 42명(정원 40). "
    "이수진: 대기자 2명은 온라인 동시 송출로 수용하자고 제안. 팀장 OK.\n2. LMS 개편 — 업체(한결소프트) "  # noqa: E501
    "1차 시안 10/15 도착 예정.\n다음 회의 10/15(목) 14시 같은 장소")
A09_T1 = (
    "다음 주 학회(한국정보과학회 추계) 구두 발표용 슬라이드 만들어 줘. 발표 15분, 10장 안팎. 아래 "
    "초록과 실험 결과로.\n\n제목: 소형 언어모델을 위한 검색 증강 생성에서의 문서 재순위화 경량화\n"
    "저자: 이서준, 김나래, 최영훈(한빛대학교 컴퓨터공학과)\n\n초록: 검색 증강 생성(RAG)은 소형 "
    "언어모델(sLM)의 사실성을 높이지만, 교차 인코더 기반 재순위화가 전체 지연 시간의 상당 부분을 "
    "차지한다. 본 연구는 교차 인코더를 지식 증류한 경량 재순위화기 LiteRerank(22M 파라미터)를 "
    "제안한다.\n\n| 재순위화 방식 | KorQuAD-RAG EM | 지연(ms/질의) |\n| 없음(BM25만) | 61.2 | 0 |\n"
    "| LiteRerank(제안) | 71.9 | 38 |")
A09_T2 = ("운영체제(학부 3학년) 50분 강의용 슬라이드 만들어 줘. 주제는 CPU 스케줄링 — FCFS, SJF(비선점), "  # noqa: E501
          "SRTF, 라운드 로빈(타임 퀀텀 2), 우선순위 스케줄링. 12장 안팎.\n아래 예제 프로세스 하나로 "  # noqa: E501
          "모든 알고리즘의 간트 차트와 평균 대기시간을 계산해서 비교해 주고, 마지막에 비교 표와 확인 "  # noqa: E501
          "퀴즈 2문제를 넣어 줘.\n\nP1: 0, 7, 3\nP2: 2, 4, 1\nP3: 4, 1, 4\nP4: 5, 4, 2")
A10_T1 = (
    "「국내 클라우드 협업툴 시장」에 진입할 만한지 검토 보고서 써 줘. 경영회의 안건 자료야.\n\n"
    "우리 회사 자료(한빛소프트, 가상 회사 — 아래는 사내 자료임):\n- 업력 12년, 직원 85명, 주력은 "
    "중견기업용 전자결재·그룹웨어(설치형), 고객사 약 340곳\n- 작년 매출 162억원, 그중 유지보수 "
    "매출 비중 58%\n- 검토안: 기존 그룹웨어에 붙는 SaaS형 협업툴(메신저·화상·공동편집) 2027년 "
    "하반기 출시\n\n알고 싶은 것: 국내 시장 규모와 성장 전망(공개 자료 기준, 추정이면 가정 명시), "
    "고객군 나누기, 주요 경쟁사(네이버웍스, 카카오워크, 잔디, 플로우, 두레이, 마이크로소프트 팀즈, "
    "슬랙 등)의 위치, 산업 환경(규제·공공 클라우드 등), 진입 여부 의견.")


# ── 1. the answer plan is optional notes, off when the person fixed the form ──────────


def test_a_fixed_form_turns_the_answer_plan_off():
    assert answer_plan.restricted(P14) and not answer_plan.wanted(P14)
    assert answer_plan.restricted(P48)
    assert not answer_plan.restricted(P16) and answer_plan.wanted(P16)
    assert answer_plan.wanted(P56)


@pytest.mark.asyncio
async def test_the_plan_never_invents_an_earlier_conversation_and_keeps_a_rule_from_turn_one():
    seen: list[str] = []

    async def complete(model, msgs, key, max_tokens):
        seen.append(msgs[-1]["content"])
        return "- 행동 활성화 과제\n- 수면 규칙\n- 상담 연결\n- 정확히: 우울감과 우울증 구분", {}

    first = [{"role": "system", "content": "s"}, {"role": "user", "content": P16}]
    outline = await answer_plan.plan(first, complete=complete, model="m", api_key="k")
    assert outline and "earlier in this conversation" not in seen[-1]
    follow = [*first, {"role": "assistant", "content": "…"}, {"role": "user", "content": P16_2}]
    await answer_plan.plan(follow, complete=complete, model="m", api_key="k")
    assert "earlier in this conversation" in seen[-1]
    # 「진단과 치료 계획만 … 설명도 쓰지 마」 in turn one still holds on turn two
    roleplay = [{"role": "user", "content": P14}, {"role": "assistant", "content": "…"},
                {"role": "user", "content": P14_2}]
    calls = len(seen)
    assert await answer_plan.plan(roleplay, complete=complete, model="m", api_key="k") == ""
    assert len(seen) == calls
    note = answer_plan.attach(first, outline)[-1]["content"]
    assert "빠짐없이" not in note and "not part of the user's request" in note
    assert "silently drop" in note


# ── 2. approval only when there is something to decide ───────────────────────────────


def test_a_specific_request_with_a_plain_outline_is_written_straight_away():
    minutes = {"title": "교육혁신팀 주간회의 회의록 (2026-10-08)", "visualStyle": "editorial",
               "sections": ["회의 개요", "결정 사항", "할 일", "안건별 논의 내용",
                            "미결 사항 및 확인 필요", "다음 회의"]}
    assert outline_rules.nothing_to_approve(minutes, A01_T1)
    deck = {"title": "LiteRerank", "slides": [{"title": f"{i}", "layout": "bullets"}
                                              for i in range(10)]}
    assert outline_rules.nothing_to_approve(deck, A09_T1)


def test_a_card_stays_for_a_real_decision():
    sections = {"sections": ["개요", "현황", "사례", "시사점"]}
    # a short request leaves the scope open
    assert not outline_rules.nothing_to_approve(sections, "생성형 AI 동향 보고서 써 줘")
    assert outline_rules.nothing_to_approve(sections, "생성형 AI 동향 보고서 써 줘",
                                            has_material=True)
    # pictures cost credits, a form marks missing facts, a long outline is worth a look
    assert not outline_rules.nothing_to_approve({**sections, "figures": [{"caption": "x"}]},
                                                A01_T1)
    assert not outline_rules.nothing_to_approve({**sections, "frame": True}, A01_T1)
    assert not outline_rules.nothing_to_approve({"sections": [str(i) for i in range(12)]},
                                                A01_T1)
    # 24 slides for 「12장 안팎」: the size is the decision
    deck = {"slides": [{"title": f"{i}", "layout": "bullets"} for i in range(24)]}
    assert not outline_rules.nothing_to_approve(deck, A09_T2)
    # the one-part 공문 still goes straight
    assert outline_rules.nothing_to_approve({"sections": ["본문"]}, "협조 요청 공문 써 줘")


def test_a_titled_request_with_its_abstract_is_not_asked_what_it_is_about():
    blank = '{"title": "LiteRerank", "subject": "", "slides": []}'
    assert not grounding.subject_missing(blank, A09_T1)
    # a short ask with no subject is still asked
    ask = "처장님 결재용 한 장 보고를 써 주세요. 결정할 것, 대안 두 개, 권고안을 담아 주세요."
    assert grounding.subject_missing('{"title": "보고", "subject": "", "sections": []}', ask)


# ── 3. pasted text is not a question about now; no footer past a fixed form ─────────


def test_text_to_proofread_is_material_not_a_present_fact():
    assert _reads_pasted_material(P36)


def test_no_footer_is_added_to_an_answer_whose_form_was_fixed():
    assert agent._form_fixed([{"role": "user", "content": P14}], P14)
    turn_two = [{"role": "user", "content": P14}, {"role": "assistant", "content": "진단: …"},
                {"role": "user", "content": P14_2}]
    assert agent._form_fixed(turn_two, P14_2)
    assert not agent._form_fixed([{"role": "user", "content": P56}], P56)


def test_advice_stays_concrete_and_yields_to_the_requested_form():
    system = freshness.with_answer_policy(
        [{"role": "user", "content": P56}], {"id": "m"}, current_fact=False, advice="tax",
    )[0]["content"]
    assert "requested form comes first" in system
    assert "even when the search found nothing" in system
    assert "never fall back to principles only" in system
    assert "Never invent a tax rule" in system


# ── 4. a market review that asks for public figures searches ──────────────────────────


def test_a_request_for_public_figures_searches_whatever_the_format_says():
    assert report.asks_public_sources(A10_T1)
    assert not report.material_complete(A10_T1, "agenda")
    # the person's own memo does not search: 「경쟁사」 in a memo line is not an ask
    memo = A01_T1 + "\n3. 경쟁사 동향 공유 — 김도윤"
    assert not report.asks_public_sources(memo)


# ── 5. the conversation's language holds on a tone request ────────────────────────────


def test_a_friendlier_english_email_is_not_rewritten_in_korean():
    history = [{"role": "user", "content": P38}, {"role": "assistant", "content": P38_FORMAL},
               {"role": "user", "content": P38_2}]
    assert agent._answer_anomaly(P38_FRIENDLY, history) is None
    # the previous answer's language counts even without the 「언어: 영어」 line
    unlabelled = [{"role": "user", "content": P38.replace("- 언어: 영어\n", "")},
                  {"role": "assistant", "content": P38_FORMAL},
                  {"role": "user", "content": P38_2}]
    assert agent._answer_anomaly(P38_FRIENDLY, unlabelled) is None
    korean = [{"role": "user", "content": "전이학습이 뭐야? 사진 300장짜리 데이터셋에 쓰면 뭘 주의해야 해?"}]  # noqa: E501
    assert agent._answer_anomaly(P38_FRIENDLY * 2, korean)[0] == "language"


# ── 6. a closing hop's line of intent is no answer ────────────────────────────────────


def test_a_closing_line_of_intent_is_a_preamble():
    ask = "네이버웍스 대응 배틀카드 만들어 줘. 공개 자료로 요금과 기능을 확인해서."
    assert agent._preamble_only("네이버웍스 요금을 확인해야 합니다.", request=ask,
                                wanted_tools=False)
    assert agent._preamble_only(
        "네이버웍스 요금·기능·고객 사례를 확인했습니다. 이제 배틀카드를 검증하겠습니다.",
        request=ask, wanted_tools=False)
    assert agent._preamble_only("요금 페이지를 한 번 더 봅니다.", request=ask, wanted_tools=True)
    assert not agent._preamble_only("네이버웍스 Standard 요금은 사용자당 월 6,000원입니다.",
                                    request=ask, wanted_tools=False)
    assert not agent._preamble_only("| 항목 | 네이버웍스 |\n| 요금 | 6,000원 |", request=ask,
                                    wanted_tools=True)


@pytest.mark.asyncio
async def test_the_closing_hop_preamble_is_replaced_by_an_answer_from_the_results(monkeypatch):
    card = "## 네이버웍스 배틀카드\n\n| 항목 | 네이버웍스 | 한빛워크 |\n|---|---|---|\n| 설치형 | 없음 | 있음 |"  # noqa: E501
    steps = [
        {"calls": [{"id": "a", "name": "lookup", "arguments": "{}"}]},
        {"calls": [{"id": "b", "name": "lookup", "arguments": "{}"}]},
        {"text": "네이버웍스 요금을 확인해야 합니다."},
        {"text": card},
    ]
    seen: list[list[dict]] = []

    async def completion(_model, messages, tools, *_args, **_kwargs):
        seen.append(copy.deepcopy(messages))
        step = steps[len(seen) - 1]
        acc = agent._Accumulator()
        acc.calls = dict(enumerate(step.get("calls", [])))
        acc.content = [step["text"]] if step.get("text") else []
        acc.usage = {"inputTokens": 1, "outputTokens": 1}
        for text in acc.content:
            yield "delta", text
        yield "done", acc

    async def run(_arguments):
        return ToolResult(content="네이버웍스 요금: Lite 무료, Standard 사용자당 월 6,000원")

    monkeypatch.setattr(agent, "_stream_once", completion)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 1)
    tool = Tool(name="lookup", description="d", parameters={"type": "object"}, run=run,
                label="lookup", read_only=True)
    ask = "네이버웍스 대응 배틀카드 만들어 줘."
    events = [e async for e in agent.run_turn(
        "m", [{"role": "user", "content": ask}], [tool],
        ToolContext(user_id="u", session_id="s", request=ask))]
    shown = ""
    for event in events:
        if event["type"] == "delta":
            shown += event["text"]
        elif event["type"] == "retract":
            at = shown.rfind(event["text"])
            shown = shown[:at] + shown[at + len(event["text"]):] if at >= 0 else shown
    assert len(seen) == 4
    assert "6,000원" in seen[-1][-1]["content"]
    assert card in shown and "확인해야 합니다" not in shown
