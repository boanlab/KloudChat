"""Round 8: the writer's English reasoning and check reports out of a chat answer, Cyrillic and
kana repaired in chat answers too, a crisis line's wrong number set by code.

All three run in `answer-normalize` for every model group (docs/architecture.md, "Mechanisms
by model group"): they are code with no judge, the slips came from self-hosted writers (flash
P05·P53·A12-T1·A16, 27B P07·P16), and on a strong external writer they find nothing to do or do
the same safe fix. Each case runs for a self-hosted writer and a strong external one."""

from __future__ import annotations

import asyncio

import pytest

from app.pipeline import chat_stages
from app.pipeline.stage import Context
from app.services import hangul, hotlines, internal_words

L27, LFL = "local/qwen3.8-27b", "local/qwen3.8-flash"
STRONG_EXT = "deepseek/deepseek-v4-pro-0813"
WRITERS = pytest.mark.parametrize("writer", [L27, LFL, STRONG_EXT])


def _normalized(answer: str, request: str, *, writer: str, material: str = "",
                evidence: list[str] | None = None) -> tuple[str, list[dict]]:
    bundle = chat_stages.bundle_for(
        answer=answer, request=request, model={"id": writer}, actual_model=None, api_key="k",
        session_id="s", today=None, first_message=request,
        messages=[{"role": "user", "content": request}], material=material, check_keys=(),
        check_material="", tool_outputs=[], judge="", kept_steps=[], usage={},
        evidence=evidence or [])

    async def go():
        return [e async for e in chat_stages.Normalize().run(bundle, Context())]

    events = asyncio.run(go())
    return bundle.document.answer, events


# ── flash's untagged English reasoning (P05) and tool-step narration (P53) ──────────────

P05_REQUEST = ("영양사로서 2인분 채식 요리를 하나 짜려고 해. 1인분에 약 500kcal이고 "
               "혈당지수(GI)가 낮은 걸로. 제안해 줄 수 있어?")
P05_NOTE = (
    "I need to design a vegetarian meal for 2 servings with around 500 kcal per serving and low "
    "glycemic index, so I'm thinking through a lentil and chickpea stew paired with whole "
    "grains, then calculating the nutritional breakdown to make sure it hits those targets.")
P05_BODY = (
    "렌틸콩·병아리콩 스튜에 퀴노아와 구운 채소를 곁들이면 1인분 약 500kcal, 낮은 GI를 함께 맞출 "
    "수 있습니다. 주재료가 통콩류와 통곡물이라 전분이 천천히 소화되고, 지방과 식이섬유가 섞여 "
    "혈당 상승이 완만해집니다.")


@WRITERS
def test_an_english_plan_before_a_korean_answer_goes(writer):
    out, _ = _normalized(f"{P05_NOTE}\n\n{P05_BODY}", P05_REQUEST, writer=writer)
    assert out == P05_BODY


def test_an_english_deliverable_the_person_asked_for_stays():
    letter = ("I'm thinking through how to thank you for the interview last week, and I want to "
              "say again how much I enjoyed meeting the team.")
    after = ("위 영문 메일은 면접 뒤 감사 인사로 쓰기 좋습니다. 이름과 날짜만 바꿔서 "
             "보내세요. 길이는 짧게 두었습니다.")
    text = f"{letter}\n\n{after}"
    assert internal_words.strip_work_notes(text, request="면접 감사 메일을 영어로 써 줘") == text


P53_REQUEST = ("Explain this SQL query for a reader who knows spreadsheets but not SQL. "
               "Do not rewrite the query unless asked.")
P53_STEP = ("Let me re-run with a fixed reference date so the 90-day window is deterministic and "
            "the edge cases actually show.")
P53_BODY = (
    "**Short version:** this query produces a ranked list of German customers who spent more "
    "than 500 (currency unspecified) on non-cancelled orders in the last 90 days, with their "
    "email, signup date, order count and total spend.")


@WRITERS
def test_an_english_answer_loses_its_tool_step_opening(writer):
    out, _ = _normalized(f"{P53_STEP}\n\n{P53_BODY}", P53_REQUEST, writer=writer)
    assert out == P53_BODY


def test_an_english_answer_that_opens_with_its_point_stays():
    text = ("Let me explain the join first: a LEFT JOIN keeps every customer.\n\n" + P53_BODY)
    assert internal_words.strip_work_notes(text, request=P53_REQUEST) == text


# ── check reports told as sentences (A16-T1, A16-T2, A12-T1) ─────────────────────────

A16_T2 = (
    "두 문서 모두 항목 7개 대비 빠짐 0, 차단 0으로 검증을 마쳤습니다. 청구서 메일 첫 적용월은 "
    "배포일 11월 4일(수) 기준으로 12월 청구분(12월 3일 발송)입니다.\n\n"
    "# ① 사내 공지 (영업·고객지원팀)")
A16_T1_LAST = ("· 구 API v1 `/messages/list`는 2027년 3월 31일 종료 예정, v2 `/messages`를 "
               "이용해 주세요.")
A16_T1 = (f"{A16_T1_LAST}\n\n---\n\n"
          "항목 9개 중 노트 7 · 뺌 2 · 빠짐 0, 점검 차단 사항 0건입니다.")
A12_T1 = ("block 0입니다. CTA의 두 항목은 자료에 있는 혜택이라 그대로 둡니다.\n\n"
          "### 페이지 카피\n\n한빛워크로 팀의 문서를 한곳에서 함께 고치세요.")


@WRITERS
def test_check_reports_leave_the_answer(writer):
    out, _ = _normalized(A16_T2, "릴리스 노트로 사내 공지와 고객 메일 써 줘", writer=writer)
    assert out.startswith("청구서 메일 첫 적용월은") and "검증을 마쳤" not in out
    out, _ = _normalized(A16_T1, "릴리스 노트 써 줘", writer=writer)
    assert out == A16_T1_LAST
    out, _ = _normalized(A12_T1, "랜딩 페이지 카피 써 줘", writer=writer)
    assert out.startswith("CTA의 두 항목은") and "block 0" not in out


def test_a_count_the_person_asked_to_check_stays():
    text = "항목 7개 대비 빠짐 0, 차단 0으로 검증을 마쳤습니다."
    assert internal_words.strip_work_notes(text, request="빠진 항목이 있는지 점검해 줘") == text


# ── Cyrillic and kana in chat answers (P07 27B light, P16 flash light) ──────────────

P07 = ("- **턱 인тягивание(chin tuck):** 앉아서 턱을 뒤로 당겨 이중턱 만드는 동작, "
       "10회 × 3세트. 경추 중립 회복에 핵심.")
P16 = "| 1 | 작은 일에도 흥미 или 즐거움이 없었는가 | 지난 2주 동안 해당하면 표시하세요 |"


@WRITERS
def test_cyrillic_in_a_chat_answer_is_repaired(writer):
    out, _ = _normalized(P07, "거북목 운동 알려 줘", writer=writer)
    assert out.startswith("- **턱(chin tuck):**") and "т" not in out
    out, _ = _normalized(P16, "우울 자가 검사 문항 보여 줘", writer=writer)
    assert "흥미 또는 즐거움" in out


def test_kana_in_a_chat_answer_goes_without_its_sentence():
    text = "볼로냐 구시가를 걷고 アーム 운동을 합니다. 다음 날은 쉽니다."
    out, _ = hangul.repair_mixed_script(text, keep_sentences=True)
    assert out == "볼로냐 구시가를 걷고 운동을 합니다. 다음 날은 쉽니다."
    # documents keep their old rule: the sentence goes
    assert hangul.repair_mixed_script(text)[0].strip() == "다음 날은 쉽니다."


def test_russian_the_person_asked_about_stays():
    text = '러시아어 "Привет мир"는 안녕 세상이라는 뜻입니다. 인사말로 자주 씁니다.'
    assert hangul.repair_mixed_script(text, "Привет мир 뜻 알려 줘")[0] == text
    code = ("```python\nprint('или')\n```\n"
            "작은 일에도 흥미가 없는지 확인하는 문항을 코드로 출력합니다. 다음 단계로 갑니다.")
    assert hangul.repair_mixed_script(code)[0] == code


# ── a crisis line's wrong number (P16 27B light) ───────────────────────────────────

P16_LINES = (
    "* **자살예방 및 정신건강 위기상담:** 1599-9195 (24시간)\n"
    "    * 자해 충동이나 자살 생각이 있을 때 (즉시 정신건강의학과 또는 자살예방상담전화 "
    "1599-9195, 확인 필요).")


@WRITERS
def test_a_wrong_suicide_line_becomes_109(writer):
    out, events = _normalized(P16_LINES, "요즘 너무 우울해. 자가 검사 해 볼 수 있어?",
                              writer=writer)
    assert "1599-9195" not in out
    assert "**자살예방 및 정신건강 위기상담:** 109 (24시간)" in out
    assert "자살예방상담전화 109)." in out
    assert any(e.get("id") == "hotline_fix" for e in events)


def test_official_and_given_numbers_stay():
    text = "자살예방상담전화 109, 생명의전화 1588-9191, 정신건강위기상담전화 1577-0199"
    assert hotlines.fix(text) == (text, [])
    given = "자살예방상담전화 1599-9195"
    assert hotlines.fix(given, evidence="우리 기관 상담 전화는 1599-9195입니다")[0] == given
    # a local center's own number is not the national line
    local = "OO구 자살예방센터 상담 02-123-4567"
    assert hotlines.fix(local)[0] == local
    assert hotlines.fix("자살 예방 교육은 2026년 3월에 120명이 들었습니다.")[1] == []


def test_the_old_line_and_its_particle():
    assert hotlines.fix("자살예방상담전화 1393으로 전화하세요.")[0] == (
        "자살예방상담전화 109로 전화하세요.")
    assert hotlines.fix("정신건강위기상담전화 1588-1234를 누르세요.")[0] == (
        "정신건강위기상담전화 1577-0199를 누르세요.")
