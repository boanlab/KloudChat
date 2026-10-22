"""A self-hosted writer's odd words: a whole word the checker would swap (「지어진 업무」 →
「정해진 업무」) stands only when the writer, asked once, agrees its word was wrong; a Chinese
question word reads as its Korean word with its particle agreeing (「중哪一个를」 → 「중 어느
것을」, not 「나일개를」); and one value offered as its own alternative (「A 또는 A」) is put
once."""

from __future__ import annotations

import json
import time

import pytest

from app.services import hangul, word_fix

_TEXT = ("결정 사항과 논의 과정에서 지어진 업무를 정리하면 다음과 같습니다. 다음과 같이 결재 요청 "
         "사항을 올립니다. 회의는 순조롭게 진행되었고 참석자들은 각자의 의견을 충분히 말했습니다.")
_FIXES = {"fixes": [{"from": "지어진 업무를", "to": "정해진 업무를", "kind": "wrong_word"},
                    {"from": "결재 요청", "to": "승인 요청", "kind": "wrong_word"}]}


@pytest.mark.asyncio
async def test_a_whole_word_swap_stands_only_when_the_writer_agrees():
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        if model == "local/judge":
            return json.dumps(_FIXES, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        asked.append(messages[-1]["content"])
        return '{"swap": [1]}', {"inputTokens": 2, "outputTokens": 1}

    out, usage = await word_fix.fix(_TEXT, model="local/judge", complete=complete,
                                    api_key="k", writer="local/writer")
    assert "정해진 업무를" in out and "결재 요청" in out
    assert len(asked) == 1 and "「지어진 업무를」 → 「정해진 업무를」" in asked[0]
    assert usage == {"inputTokens": 3, "outputTokens": 2}
    # no writer to ask, or an answer that is no JSON: the checker's swap alone is refused
    for writer, answer in (("", '{"swap": [1, 2]}'), ("local/writer", "네")):
        async def other(model, messages, api_key, max_tokens, answer=answer):
            if model == "local/judge":
                return json.dumps(_FIXES, ensure_ascii=False), {}
            return answer, {}

        out, _ = await word_fix.fix(_TEXT, model="local/judge", complete=other, api_key="k",
                                    writer=writer)
        assert out == _TEXT


def test_a_chinese_question_word_reads_as_its_korean_word():
    assert hangul.read_back("구글폼과 네이버폼 중哪一个를 사용할지")[0] == \
        "구글폼과 네이버폼 중 어느 것을 사용할지"


def test_a_value_offered_as_its_own_alternative_is_put_once():
    row = ("| 설문 도구 선택 | 결정 못 함 | 2026-10-21(화, 확인 필요: 달력상 수요일) 또는 "
           "2026-10-21(화, 확인 필요: 달력상 수요일) | 재논의 |")
    assert word_fix.twin_alternatives(row) == \
        "| 설문 도구 선택 | 결정 못 함 | 2026-10-21(화, 확인 필요: 달력상 수요일) | 재논의 |"
    assert word_fix.twin_alternatives("구글폼이나 구글폼으로 정합니다.") == "구글폼으로 정합니다."
    for kept in ("구글폼 또는 네이버폼", "회의 또는 회의록", "다음 주 또는 다음 달", "1 또는 1"):
        assert word_fix.twin_alternatives(kept) == kept


def test_the_twin_check_finishes_on_a_long_run_of_spaces():
    for text in (" " * 50_000, " 또는 " * 10_000, "가나 " * 10_000):
        start = time.perf_counter()
        word_fix.twin_alternatives(text)
        assert time.perf_counter() - start < 0.5
