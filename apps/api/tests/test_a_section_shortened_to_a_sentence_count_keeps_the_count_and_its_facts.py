"""「2절을 두 문장으로 줄여줘」: a rewrite with another sentence count or without the section's
figures and names is sent back to the writer once with the reason; what still misses is said
in the reply."""

from __future__ import annotations

import time

import pytest

from app.pipeline import revise_stages, save_stages
from app.pipeline.stage import Context
from app.services import deck, revise

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
    # a list row is not a sentence
    assert revise.sentence_count("5.5% 올랐다. 「확정」이다.」\n- 남은 일") == 2
    assert revise.lost_facts(BEFORE, ONE) == ["10/16", "5%", "4%", "2건", "420만", "A사"]
    assert revise.shortening_faults(BEFORE, TWO, ASK) == []
    assert revise.shortening_faults(BEFORE, BEFORE, "2절 표현을 다듬어줘") == []
    faults = revise.shortening_faults(BEFORE, ONE, ASK)
    assert faults[0] == ("The section must be exactly 2 sentences of its own content; the last "
                         "version had 1")
    assert "5%, 4%" in faults[1]
    started = time.monotonic()
    revise.sentences_asked("두" + " " * 50000 + "문장")
    revise.lost_facts("A" + " " * 50000, "1" + " " * 50000)
    assert time.monotonic() - started < 0.5


PARTS = [{"id": "s0", "heading": "개요", "content": "주간 보고입니다.", "format": "markdown"},
         {"id": "s1", "heading": "금주 진행 현황", "content": BEFORE, "format": "markdown"}]


def _bundle(ask: str = ASK):
    return revise_stages.bundle_for(
        request="주간 업무 보고서를 만들어줘.", instruction=ask, model={"id": "m"},
        planner=None, api_key="k", is_deck=False, title="주간 업무 보고", data={},
        parts=[dict(p) for p in PARTS], material=[],
        plan=revise.Plan(scope="parts", targets=[1], note=ask, clauses={1: [ask]}),
        format_id="", usage={"inputTokens": 0, "outputTokens": 0})


async def _rewrite(monkeypatch, answers, ask: str = ASK):
    notes: list[str] = []
    given = iter(answers)

    async def rewrite_section(**kwargs):
        notes.append(kwargs["note"])
        return next(given), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    bundle = _bundle(ask)
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    return bundle.document, notes


@pytest.mark.asyncio
async def test_a_one_sentence_rewrite_is_sent_back_with_the_reason(monkeypatch):
    doc, notes = await _rewrite(monkeypatch, [ONE, TWO])
    assert len(notes) == 2
    assert "exactly 2 sentences of its own content; the last version had 1" in notes[1]
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
        "2절에 원래 없던 3주차가 들어갔습니다. 확인해 주세요. "
        "2절에서 10/16, 5%, 4%, 2건, 420만, A사가 빠졌습니다. 필요하면 다시 넣어 달라고 해 주세요.")
    assert save_stages.revision_reply(doc, None, is_deck=False, english=True) == (
        "Revised section 2. Section 2 has 1 sentence, not the 2 asked. Section 2 now states "
        "3주차, which the section and the material did not; please check. On section 2, 10/16, 5%, "
        "4%, 2건, 420만, A사 dropped out of the rewrite; ask again to put them back if they "
        "belong there.")


@pytest.mark.asyncio
async def test_a_plain_shortening_is_not_sent_back_and_its_dropped_facts_are_said(monkeypatch):
    doc, notes = await _rewrite(monkeypatch, [ONE], ask="2절을 줄여줘")
    assert len(notes) == 1 and doc.sections[1]["content"] == ONE
    assert doc.counted == [] and doc.dropped == ["2절 10/16, 5%, 4%, 2건, 420만, A사"]


TABLED = ("목요일에는 대성유통에게 견적서를 발송했으며, 경쟁사 A사가 4% 낮은 단가를 "
          "제시했습니다.\n\n"
          "**표 1. 일별 업무 추진 현황**\n\n| 일정 | 내용 |\n|---|---|\n"
          "| 목 | 대성유통 견적서 발송, 경쟁사 A사 4% 낮음 |")
CUT = ("대성유통 견적서를 보냈습니다(표 1).\n\n**표 1. 일별 업무 추진 현황**\n\n| 일정 | 내용 |\n"
       "|---|---|\n| 목 | 대성유통 견적서 발송, 경쟁사 A사 4% 낮음 |")
#: the retry that wrote the reason into the section and a date from nowhere (reverify-o)
ECHOED = "2절을 두 문장만 유지해야 합니다.\n\n10월 13일 기준 " + TWO


def test_facts_still_in_the_sections_table_are_not_called_dropped():
    assert revise.lost_facts(TABLED, CUT) == []
    assert revise.lost_facts(TABLED, CUT.split("\n\n")[0]) == ["4%", "A사"]


def test_the_ask_restated_in_the_section_is_taken_out_before_counting():
    kept = revise.without_ask_echo(ECHOED)
    assert not kept.startswith("2절을") and revise.sentence_count(kept) == 2
    # a section that names another section by number is left alone
    assert revise.without_ask_echo("자세한 일정은 3절에 정리했습니다.") == (
        "자세한 일정은 3절에 정리했습니다.")
    started = time.monotonic()
    revise.without_ask_echo("2절 " + " " * 50000 + "문장")
    revise.added_facts("1" + " " * 50000, "2" + " " * 50000, "")
    assert time.monotonic() - started < 0.5


def test_a_figure_or_date_from_nowhere_is_a_fault():
    # 10 is in the section's 10/16: only the day comes from nowhere
    assert revise.added_facts(BEFORE, "10월 13일 기준 " + TWO, "") == ["13일"]
    assert revise.added_facts(BEFORE, TWO, "") == []
    faults = revise.shortening_faults(BEFORE, "10월 13일 기준 " + TWO, ASK)
    assert any("Add no figure or date" in f and "13일" in f for f in faults)
    assert faults[-1].startswith("Write none of these remarks")


@pytest.mark.asyncio
async def test_an_echoed_ask_and_an_invented_date_are_sent_back_then_said(monkeypatch):
    doc, notes = await _rewrite(monkeypatch, [ECHOED, ECHOED])
    assert len(notes) == 2 and "13일" in notes[1]
    assert not doc.sections[1]["content"].startswith("2절을")
    assert doc.counted == [] and doc.added == ["2절 13일"]
    assert save_stages.revision_reply(doc, None, is_deck=False) == (
        "2절을 고쳤습니다. 2절에 원래 없던 13일이 들어갔습니다. 확인해 주세요.")


#: one sentence and the table (reverify-p): one sentence, not three
WITH_TABLE = ("3분기 팀 매출은 41.2억원으로 목표 40억 대비 103%를 달성해, 전분기 38.5억 대비 7% "
              "성장했습니다(표 2).\n\n**표 2. 진행 사항**\n\n| 일정 | 내용 |\n| --- | --- |\n"
              "| 월 | 대성유통 1차 미팅 (5% 인하 요구) |\n\n- 남은 일: CRM 병합")


def test_only_prose_sentences_are_counted():
    assert revise.sentence_count(WITH_TABLE) == 1
    faults = revise.shortening_faults(WITH_TABLE, WITH_TABLE, ASK)
    assert faults[0].endswith("the last version had 1")
    started = time.monotonic()
    revise.sentence_count("**" + "a" * 50000)
    revise.sentence_count("표 1" + " " * 50000)
    assert time.monotonic() - started < 0.5


def test_figures_are_real_figures_and_money_is_compared_as_money():
    said = [s for *_, s in deck.figures_in("표 1의 항목, 2.7억원입니다, 3곳에서, 목표 40억 대비")]
    assert said == ["2.7억", "3곳", "40억"]
    assert revise.lost_facts("전분기 38.5억 원", "전분기 38억 5천만 원보다 늘었다") == []
    assert revise.lost_facts("전분기 38.5억 원", "전분기 3,850,000,000원") == []
    assert revise.lost_facts("전분기 38.5억원보다", "38.5 억 원") == []
    assert revise.lost_facts("전분기 38.5억 원", "전분기 38억 원") == ["38.5억"]
    started = time.monotonic()
    deck.amounts_in("1억" + " " * 50000 + "5")
    assert time.monotonic() - started < 0.5
