"""「2절을 두 문장으로 줄여줘」: a rewrite with another sentence count or without the section's
figures and names is sent back to the writer once with the reason; what still misses is said
in the reply."""

from __future__ import annotations

import time

import pytest

from app.pipeline import revise_stages, save_stages
from app.pipeline.stage import Context
from app.services import revise

BEFORE = ("대성유통 협상은 견적서 발송(10/16 목)을 완료했으며, 상대 측의 5% 단가 인하 요구와 자사 "
          "허용 한도인 4% 사이의 폭이 남아 있습니다. 경쟁사 A사의 동일 품목 4% 낮은 단가 제시는 "
          "확정되지 않은 정보입니다.\n\n한솔마트 클레임은 2건을 모두 처리해 반환금 420만원 정산을 "
          "마쳤습니다.")
#: one sentence, the negotiation facts gone (reverify-m)
ONE = ("2026년 10월 3주차에는 대성유통 협상, 한솔마트 클레임 처리, CRM 데이터 정리가 함께 "
       "진행됩니다.")
TWO = ("대성유통은 10/16 견적서를 보냈고 5% 인하 요구와 4% 한도 사이의 폭, A사의 4% 낮은 단가 "
       "제시가 남았습니다. 한솔마트 클레임 2건은 420만원 정산으로 마쳤습니다.")
ASK = "2절을 두 문장으로 줄여줘"


@pytest.mark.parametrize("words, asked", [
    (ASK, (2, False)), ("2절을 2문장으로 요약해줘", (2, False)),
    ("세 문장 이내로 줄여줘", (3, True)), ("Shorten section 2 to two sentences", (2, False)),
    ("3줄 요약을 넣어줘", (0, False)), ("두 문장을 하나로 합쳐줘", (0, False)),
])
def test_the_sentence_count_an_ask_names_is_read(words, asked):
    assert revise.sentences_asked(words) == asked


def test_a_shortened_section_is_checked_for_its_count_and_facts():
    assert revise.sentence_count(BEFORE) == 3 and revise.sentence_count(ONE) == 1
    assert revise.sentence_count("5.5% 올랐다. 「확정」이다.」\n- 남은 일") == 3
    assert revise.lost_facts(BEFORE, ONE) == ["10/16", "5%", "4%", "2건", "420만", "A사"]
    assert revise.shortening_faults(BEFORE, TWO, ASK) == []
    assert revise.shortening_faults(BEFORE, BEFORE, "2절 표현을 다듬어줘") == []
    faults = revise.shortening_faults(BEFORE, ONE, ASK)
    assert faults[0] == "The request asks for exactly 2 sentences; this version has 1"
    assert "5%, 4%" in faults[1]
    started = time.monotonic()
    revise.sentences_asked("두" + " " * 50000 + "문장")
    revise.lost_facts("A" + " " * 50000, "1" + " " * 50000)
    assert time.monotonic() - started < 0.5


PARTS = [{"id": "s0", "heading": "개요", "content": "주간 보고입니다.", "format": "markdown"},
         {"id": "s1", "heading": "금주 진행 현황", "content": BEFORE, "format": "markdown"}]


def _bundle():
    return revise_stages.bundle_for(
        request="주간 업무 보고서를 만들어줘.", instruction=ASK, model={"id": "m"},
        planner=None, api_key="k", is_deck=False, title="주간 업무 보고", data={},
        parts=[dict(p) for p in PARTS], material=[],
        plan=revise.Plan(scope="parts", targets=[1], note=ASK, clauses={1: [ASK]}),
        format_id="", usage={"inputTokens": 0, "outputTokens": 0})


async def _rewrite(monkeypatch, answers):
    notes: list[str] = []
    given = iter(answers)

    async def rewrite_section(**kwargs):
        notes.append(kwargs["note"])
        return next(given), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    bundle = _bundle()
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    return bundle.document, notes


@pytest.mark.asyncio
async def test_a_one_sentence_rewrite_is_sent_back_with_the_reason(monkeypatch):
    doc, notes = await _rewrite(monkeypatch, [ONE, TWO])
    assert len(notes) == 2 and "exactly 2 sentences; this version has 1" in notes[1]
    assert "A사" in notes[1]
    assert doc.sections[1]["content"].startswith("대성유통은 10/16")
    assert doc.counted == [] and doc.dropped == []
    assert save_stages.revision_reply(doc, None, is_deck=False) == "2절을 고쳤습니다."


@pytest.mark.asyncio
async def test_what_still_misses_after_the_second_try_is_said(monkeypatch):
    doc, _ = await _rewrite(monkeypatch, [ONE, ONE])
    assert doc.counted == [(2, 2, 1)]
    assert save_stages.revision_reply(doc, None, is_deck=False) == (
        "2절을 고쳤습니다. 2절은 요청한 2문장이 아니라 1문장입니다. "
        "2절에서 10/16, 5%, 4%, 2건, 420만, A사가 빠졌습니다. 필요하면 다시 넣어 달라고 해 주세요.")
    assert save_stages.revision_reply(doc, None, is_deck=False, english=True) == (
        "Revised section 2. Section 2 has 1 sentence, not the 2 asked. On section 2, 10/16, 5%, "
        "4%, 2건, 420만, A사 dropped out of the rewrite; ask again to put them back if they "
        "belong there.")
