"""The web check keeps a 「틀림」 only on a page about the same thing whose words show it;
the question check reads assessment items, not a lesson plan's class questions."""

from __future__ import annotations

import asyncio
import json

from app.services import fact_check, research, verify


def _doc(content: str) -> verify.Doc:
    return verify.Doc([{"heading": "시장", "content": content}])


def _gateway(monkeypatch, verdicts):
    async def available():
        return True

    async def evidence(query):
        return [{"title": "1인가구 통계", "url": "https://x/a",
                 "text": "42.3%는 1인 세대 비율이며 실제 생활 단위인 1인가구 비율은 36.6%이다."}]

    replies = iter([json.dumps([{"i": 0, "query": "1인 세대 비율"}]), *verdicts])

    async def complete(model, messages, api_key, max_tokens):
        return next(replies), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(research, "available", available)
    monkeypatch.setattr(fact_check, "_evidence", evidence)
    monkeypatch.setattr(fact_check, "_complete", lambda: complete)


SENTENCE = "1~2인 가구 비중이 전체 가구의 42.3%에 달합니다."


def test_a_contradiction_quoted_from_its_page_is_taken(monkeypatch):
    _gateway(monkeypatch, [json.dumps({
        "same_subject": True, "verdict": "틀림", "quote": "42.3%는 1인 세대 비율이며",
        "why": "1인 세대 비율이다", "source": "A"}, ensure_ascii=False)])
    found, _ = asyncio.run(fact_check.web_facts("judge", "k")(_doc(SENTENCE)))
    assert [f.sentence for f in found] == [SENTENCE] and "1인 세대" in found[0].why


def test_a_verdict_on_another_subject_or_with_an_invented_quote_is_dropped(monkeypatch):
    for verdict in (
        {"same_subject": False, "verdict": "틀림", "quote": "42.3%는 1인 세대 비율이며"},
        {"same_subject": True, "verdict": "틀림", "quote": "튀니지 재생에너지 1.62기가와트"},
    ):
        _gateway(monkeypatch, [json.dumps(verdict, ensure_ascii=False)])
        found, _ = asyncio.run(fact_check.web_facts("judge", "k")(_doc(SENTENCE)))
        assert found == []


def test_a_lesson_plans_class_questions_are_not_assessment_items():
    plan = ("| 단계 | 교수 활동 | 학생 활동 | 시간 |\n| --- | --- | --- | --- |\n"
            "| 도입 | 물 한 컵을 보여 주며 「물은 원소일까?」 질문 | 예상 발표 | 10 |")
    quiz = ("| 번호 | 문항 | 정답 |\n| --- | --- | --- |\n"
            "| 1 | 다음 중 원소는 무엇인가? ① 물 ② 산소 | ② |")
    items = fact_check._items(verify.Doc([{"heading": "a", "content": f"{plan}\n\n{quiz}"}]))
    assert [b for _, b in items] == ["| 1 | 다음 중 원소는 무엇인가? ① 물 ② 산소 | ② |"]
