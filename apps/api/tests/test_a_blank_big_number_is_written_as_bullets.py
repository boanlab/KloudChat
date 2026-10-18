"""A big-number slide whose value is only a blank (「[금액]」) is written once more as bullets."""

from __future__ import annotations

import json

import pytest

from app.services import deck

REQUEST = ("카페테리아 리뉴얼 발표 슬라이드 만들어 줘. 예산은 [금액]이고, "
           "직원 설문에서 응답자의 다수가 대화하기 좋은 자리를 원했다.")
BLANK = {"title": "설문 결과와 예산", "layout": "big-number", "metrics": [["[금액]", "총 예산"]],
         "body": "", "notes": "예산은 별도로 정합니다."}
BULLETS = {"bullets": ["직원 다수가 대화하기 좋은 자리를 원함", "총 예산: [금액]"],
           "notes": "설문에서 응답자의 다수가 대화하기 좋은 자리를 원했습니다."}


def test_only_blank_values_count():
    assert deck.blank_numbers(BLANK)
    assert not deck.blank_numbers({**BLANK, "metrics": [["3억 원", "총 예산"]]})
    assert not deck.blank_numbers({**BLANK, "metrics": [["[금액]", "예산"], ["120석", "좌석"]],
                                   "layout": "metrics"})
    assert not deck.blank_numbers({**BLANK, "layout": "bullets"})
    assert deck.blank_numbers({"layout": "statement", "title": "[금액] 소요", "body": "승인 요청"})
    assert not deck.blank_numbers({"layout": "statement", "title": "120석을 세 구역으로"})


@pytest.mark.asyncio
async def test_a_blank_big_number_becomes_bullets(monkeypatch):
    draft = {"slides": [{"title": "카페테리아 리뉴얼", "layout": "title"}, BLANK]}

    async def complete(model, messages, api_key, max_tokens=0, **kwargs):
        text = messages[-1]["content"]
        if "a blank is not a figure" in text:
            complete.asked += 1
            return json.dumps(BULLETS, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        return json.dumps(draft, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    complete.asked = 0

    async def no_figures(**kwargs):
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(deck, "_complete", complete)
    monkeypatch.setattr(deck.diagrams, "plan", no_figures)
    events = [e async for e in deck._write_slides(
        plan=[{"title": s["title"], "layout": s["layout"]} for s in draft["slides"]],
        title="카페테리아 리뉴얼", subtitle="", accent="", request=REQUEST, model="m", api_key="k",
        trusted_context=[], untrusted_context=[], usage={"inputTokens": 0, "outputTokens": 0})]
    slides = next(e["slides"] for e in events if e["type"] == "deck")
    last = next(s for s in slides if s["title"] == "설문 결과와 예산")
    assert complete.asked == 1
    assert last["layout"] == "bullets" and "metrics" not in last
    assert "총 예산: [금액]" in last["bullets"]
