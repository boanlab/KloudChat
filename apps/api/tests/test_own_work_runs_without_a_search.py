"""A draft, edit, translation or code fix of the person's own facts runs without a web
search; a current fact still searches; a cited answer carries no training-cutoff notice."""

from __future__ import annotations

import pytest
from test_exact_facts_are_searched_and_traced import _route

from app.pipeline import chat_stages
from app.pipeline.stage import Context
from app.services import exact_facts, freshness

AGENDA = (
    "다음 주 월요일 오전에 '신규 모바일 앱 출시 준비 점검 회의'를 해. 참석자는 기획 2명, "
    "개발 3명, 디자인 1명, QA 1명, 마케팅 1명이고 1시간이야. 출시 목표일은 11월 20일이고 "
    "지금 QA에서 치명 버그 3건이 열려 있어. 회의 안건(아젠다)을 시간 배분이랑 같이 짜줘."
)
BUG_FIX = (
    "This function is supposed to return the average of each student's top 3 scores, but it "
    "gives wrong results for some students and crashes for others. Fix it and explain what "
    "was wrong.\n\n```python\ndef top3_avg(scores):\n    result = {}\n"
    "    for name, s in scores.items():\n        s.sort()\n        top = s[:3]\n"
    "        result[name] = sum(top) / 3\n    return result\n\n"
    "print(top3_avg({'ann': [90, 85, 70, 100], 'bob': [60, 75], 'cy': []}))\n```"
)
PROOFREAD = (
    "Please proofread the English paragraph below, show me what you changed and why "
    "(briefly), and then translate the corrected version into natural Korean for a company "
    "newsletter.\n\n\"Our team have been working on the new onboarding flow since last "
    "month, and we are happy to announce that it will be launch on November 3rd.\""
)
MINIMUM_WAGE = ("지금 기준으로 한국 최저임금(2026년 적용분)이 얼마인지, 그리고 전년 대비 몇 % "
                "올랐는지 알려줘. 출처도 같이.")


@pytest.mark.parametrize("ask", [
    AGENDA, BUG_FIX, PROOFREAD,
    "팀 송년회 공지 메일 초안 써줘. 12월 19일 금요일 저녁 7시, 회사 앞 식당이야.",
])
def test_a_draft_an_edit_or_a_code_fix_of_given_facts_is_own_work(ask):
    assert exact_facts.own_work(ask)


@pytest.mark.parametrize("ask", [
    MINIMUM_WAGE,
    "최신 환율 반영해서 해외 거래처에 보낼 견적 메일 써줘.",
    "요즘 마케팅 트렌드 찾아서 회의 안건 짜줘.",
    "오늘 서울 날씨 알려주고 야유회 공지 써줘.",
    "요즘 잠이 부족해서 하루에 커피를 4~5잔 마시는데 괜찮은 건가요?",
    "친구 결혼식 축의금을 얼마 해야 할지 고민이에요.",
    "두통 증상이 계속돼서 병가 메일 써줘.",
])
def test_a_lookup_a_world_value_or_advice_is_not_own_work(ask):
    assert not exact_facts.own_work(ask)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [AGENDA, BUG_FIX])
async def test_an_agenda_or_a_bug_fix_on_auto_neither_forces_nor_offers_a_search(
        monkeypatch, content):
    captured = await _route(monkeypatch, content)
    assert captured["force_tool"] is None
    assert captured["preset_call"] is None
    assert "web_search" not in {tool.name for tool in captured["tools"]}
    assert captured["freshness_request"] is None


@pytest.mark.asyncio
async def test_a_current_fact_still_searches_first(monkeypatch):
    captured = await _route(monkeypatch, MINIMUM_WAGE)
    assert (captured["force_tool"] == "web_search"
            or (captured["preset_call"] or ("",))[0] == "web_search")


@pytest.mark.asyncio
async def test_search_turned_on_still_searches_a_draft(monkeypatch):
    captured = await _route(monkeypatch, AGENDA, toggle=True)
    assert (captured["force_tool"] == "web_search"
            or (captured["preset_call"] or ("",))[0] == "web_search")


# ── the training-cutoff notice under a cited answer ──────────────────────

MODEL = {"id": "local/qwen3.8-flash"}
NOTICE = freshness.accuracy_caveat("질문", MODEL, MODEL["id"])
SOURCES = (
    "\n\n### 출처\n"
    "- [1] [카페인 섭취 · bokjiro.go.kr](https://www.bokjiro.go.kr/a) · 확인 2026-10-20\n"
    "- [2] [카페인 - 식품안전나라 · foodsafetykorea.go.kr](https://www.foodsafetykorea.go.kr/b)"
    " · 확인 2026-10-20"
)
CITED = "성인 하루 카페인 권장 상한은 400mg입니다 [[1]](https://www.bokjiro.go.kr/a)."


def test_the_notice_above_the_turns_source_list_goes():
    answer = CITED + "\n\n" + NOTICE + SOURCES
    assert freshness.normalize_answer_notice(answer, "질문", MODEL, MODEL["id"]) == (
        CITED + SOURCES)


def test_the_notice_under_an_uncited_answer_stays():
    plain = "기준금리는 변동될 수 있습니다."
    answer = plain + "\n\n" + NOTICE
    assert freshness.normalize_answer_notice(answer, "질문", MODEL, MODEL["id"]) == answer


async def _normalized(answer: str, *, evidence=(), outputs=()) -> str:
    bundle = chat_stages.bundle_for(
        answer=answer, request="카페인 괜찮나요?", model=MODEL, actual_model=MODEL["id"],
        api_key="k", session_id="s", today=None, first_message="카페인 괜찮나요?",
        messages=[{"role": "user", "content": "카페인 괜찮나요?"}], material="",
        check_keys=(), check_material="", tool_outputs=list(outputs), judge="",
        kept_steps=[], usage={}, evidence=list(evidence),
    )
    await chat_stages.Normalize().apply(bundle, Context())
    return bundle.document.answer


@pytest.mark.asyncio
async def test_a_page_the_model_read_this_turn_grounds_the_answer():
    body = "성인 하루 카페인 권장 상한은 400mg입니다."
    answer = await _normalized(body + "\n\n" + NOTICE,
                               outputs=["https://www.mfds.go.kr/caffeine\n카페인 400mg"])
    assert NOTICE not in answer
    kept = await _normalized(body + "\n\n" + NOTICE)
    assert NOTICE in kept
