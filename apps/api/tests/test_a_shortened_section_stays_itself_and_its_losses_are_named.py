"""A shortened section stays the same section: the writer sees its current text, a line to the
person (「다음은 요청하신 대로 …」) is taken out, a version that reads as another section or
points at a table it lacks is sent back and then kept out; figures and boxes it drops are
named. English figures are read whole (「1K」 = 「1,000」, no 「3.8」 out of 「Qwen3.8」), a
question while a card is open leaves the card, and a table's rows are not joined."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.models.chat import SessionKind
from app.pipeline import revise_stages, save_stages
from app.pipeline.stage import Context
from app.services import deck, report, revise

FIRST = ("팀 매출 41.2억 원으로 분기 목표 40억 원을 103% 달성했습니다. "
         "신규 거래처 대성유통은 월 발주 예상 3,200만 원 규모로 견적서를 발송했습니다."
         "\n\n**표 1. 주간 업무 현황**\n\n"
         "| 사업 | 상태 |\n|---|---|\n| 대성유통 1차 미팅 견적서 발송 | 진행 중 |")
SECOND = ("상대방의 단가 인하 요구는 5%이며, 현재 협상이 남아 있는 상태입니다. "
          "경쟁사 A사의 4% 낮은 단가 정보는 확정이 아닌 파악 단계입니다.\n\n"
          "클레임 발생 원인은 9월 물류센터 이전 시 출고 지연으로 기록되어 있습니다. "
          "영남 지역 출고 지연 재발 여부는 물류팀 확인이 필요합니다.")
#: the rewrite that became section 1 (reverify-q t1b)
DRIFTED = ("다음은 요청하신 대로 내용을 축약한 '주간 업무 현황' 절입니다.\n\n"
           "본 주에는 신규 거래처 대성유통 1차 미팅과 견적서 발송, 팀 매출 41.2억 원 마감 자료를 "
           "정리했습니다. 월 발주 예상 3,200만 원 규모입니다.\n\n위 표에서 확인하듯 대부분 "
           "마무리 단계입니다.")
SHORT = ("대성유통의 5% 인하 요구와 A사의 4% 낮은 단가 정보 사이에서 협상이 남아 있습니다. "
         "9월 물류센터 이전에 따른 출고 지연 재발 여부는 물류팀 확인이 필요합니다.")
BOXED = (SECOND + "\n\n```callout\n단가 승인 지연 시 협상 교착\n결재 전에는 확정하지 못합니다.\n```"
         "\n\n![그림 1. 단가 협상 범위](data:image/png;base64,AAAA)\n\n그림 1. 단가 협상 범위")


def test_a_line_to_the_person_is_taken_out():
    assert revise.without_preamble(DRIFTED).startswith("본 주에는")
    assert revise.without_preamble(SHORT) == SHORT
    started = time.monotonic()
    revise.without_preamble("다음은" + " " * 50000 + "요청")
    revise.drift_faults("위" + " " * 50000, "위" + " " * 50000 + "표", [])
    assert time.monotonic() - started < 0.5


def test_a_rewrite_that_reads_as_another_section_is_found():
    faults = revise.drift_faults(SECOND, revise.without_preamble(DRIFTED), [FIRST])
    assert any("another section" in f for f in faults)
    assert any("does not have" in f for f in faults)
    assert revise.drift_faults(SECOND, SHORT, [FIRST]) == []
    assert revise.drift_faults(SECOND, SHORT + " 자세한 수치는 표 7과 같습니다.", [FIRST]) == [
        "It cites 표 7, which the document does not have"]


def test_figures_and_boxes_a_shortening_dropped_are_named():
    assert revise.removed_blocks(BOXED, SHORT, 2) == [
        "2절의 그림 1", "2절의 「단가 승인 지연 시 협상 교착」 상자"]
    retitled = SHORT + "\n\n```callout\n결재 대기\n승인 전입니다.\n```"
    assert revise.removed_blocks(BOXED, retitled, 2) == ["2절의 그림 1"]
    assert save_stages._en_item("2절의 「결재 대기」 상자") == (
        "the box 「결재 대기」 from section 2")


PARTS = [{"id": "s0", "heading": "이번 주 주요 성과", "content": FIRST, "format": "markdown"},
         {"id": "s1", "heading": "주간 업무 현황", "content": SECOND, "format": "markdown"}]


async def _shorten(monkeypatch, answers, before=SECOND):
    notes: list[str] = []
    given = iter(answers)

    async def rewrite_section(**kwargs):
        notes.append(kwargs["note"])
        return next(given), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    parts = [dict(p) for p in PARTS]
    parts[1]["content"] = before
    bundle = revise_stages.bundle_for(
        request="주간 업무 보고서", instruction="2절을 줄여줘", model={"id": "m"}, planner=None,
        api_key="k", is_deck=False, title="주간 보고", data={}, parts=parts, material=[],
        plan=revise.Plan(scope="parts", targets=[1], note="2절을 줄여줘",
                         clauses={1: ["2절을 줄여줘"]}),
        format_id="", usage={"inputTokens": 0, "outputTokens": 0})
    bundle.plan.order = [1]
    events: list[dict] = []
    try:
        async for event in revise_stages.Rewrite().run(bundle, Context()):
            events.append(event)
    except revise_stages.Stop:
        pass
    return bundle.document, notes, events


@pytest.mark.asyncio
async def test_a_shortening_that_became_another_section_is_sent_back_then_kept_out(monkeypatch):
    doc, notes, events = await _shorten(monkeypatch, [DRIFTED, DRIFTED])
    assert len(notes) == 2 and "another section" in notes[1]
    assert doc.sections[1]["content"] == SECOND and doc.failed == ["주간 업무 현황"]
    assert any(e.get("message", "").startswith("고친 글이 검사를 통과하지 못해 이전 버전을 그대로")
               for e in events if e["type"] == "error")
    doc, _, _ = await _shorten(monkeypatch, [DRIFTED, SHORT])
    assert doc.sections[1]["content"] == SHORT and doc.failed == []


@pytest.mark.asyncio
async def test_a_dropped_box_is_said_and_the_picture_stays(monkeypatch):
    doc, _, _ = await _shorten(monkeypatch, [SHORT], before=BOXED)
    # the picture, not shown to the writer, is put back; the box it left out is said
    assert "![그림 1. 단가 협상 범위](data:image/png;base64,AAAA)" in doc.sections[1]["content"]
    assert doc.removed == ["2절의 「단가 승인 지연 시 협상 교착」 상자"]
    assert save_stages.revision_reply(doc, None, is_deck=False).startswith(
        "2절의 「단가 승인 지연 시 협상 교착」 상자를 뺐습니다. 2절을 고쳤습니다.")


@pytest.mark.asyncio
async def test_the_writer_sees_the_sections_own_text(monkeypatch):
    seen: list[str] = []

    async def complete(_model, messages, _key, _limit):
        seen.append(" ".join(str(m.get("content")) for m in messages))
        return SHORT, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    await report.rewrite_section(request="주간 보고", heading="주간 업무 현황",
                                 sections=[dict(p) for p in PARTS], target_id="s1", model="m",
                                 api_key="k", note="줄인다")
    assert "This section as it stands now" in seen[0]
    assert "클레임 발생 원인은 9월 물류센터 이전" in seen[0]


def test_english_figures_are_read_whole_and_compared_as_numbers():
    old = {"layout": "bullets", "bullets": [
        "Cost / 1K docs: $4", "Qwen3.8-27B handles 262K context", "Gemma 4 E2B offers 128K"]}
    new = {"layout": "bullets", "bullets": ["Cost per 1,000 docs: $4", "Qwen3.8-27B is local"]}
    assert deck.lost_figures(old, new) == ["262K", "128K"]
    assert "3.8" not in [s for *_, s in deck.figures_in("Qwen3.8-27B and E2B")]
    assert "2B" not in [s for *_, s in deck.figures_in("Qwen3.8-27B and E2B")]


def test_a_question_while_any_card_is_open_leaves_the_card():
    from app.routers import sessions

    def session(stage):
        return SimpleNamespace(pending={"stage": stage}, artifact_id="a1", kind=SessionKind.slides)

    payload = SimpleNamespace(content="4장 수치는 어디서 나온 거야?", approve=False, answers=None)
    assert sessions._sets_card_aside(session("plan"), payload)
    note = SimpleNamespace(content="4장은 빼고 진행해 줘", approve=False, answers=None)
    # a note on a proposed outline is still a note on it
    assert not sessions._sets_card_aside(session("plan"), note)


@pytest.mark.asyncio
@pytest.mark.parametrize("ask, said_as", [
    ("2장 항목을 하나로 합쳐줘",
     "바꾼 것이 없습니다. 요청한 2장 항목 합치기는 표의 행은 합칠 항목 목록이"),
    # a split asked is said as a split (reverify-x kd2)
    ("2장 항목을 두 개로 나눠줘",
     "바꾼 것이 없습니다. 요청한 2장 항목 나누기는 표의 행은 나눌 항목 목록이"),
    ("Split the items on slide 2 into two", "Nothing was changed: splitting the items on slide 2 "
     "could not be made from the slide's own points (a table's rows are not a list of items to "
     "split)"),
])
async def test_a_tables_rows_are_not_joined_and_that_is_said(monkeypatch, ask, said_as):
    async def rewrite_slide(**_kwargs):
        raise AssertionError("a table is not rewritten for a join")

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    table = {"id": "x", "layout": "table", "title": "실적 vs 목표",
             "rows": [["항목", "3분기", "4분기"], ["매출", "128억", "140억"]]}
    bundle = revise_stages.bundle_for(
        request="보고", instruction=ask, model={"id": "m"}, planner=None,
        api_key="k", is_deck=True, title="보고", data={},
        parts=[{"id": "t", "layout": "title", "title": "보고"}, dict(table)], material=[],
        plan=revise.Plan(scope="parts", targets=[1], note="합치기"), format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})
    bundle.plan.order = [1]
    events = []
    with pytest.raises(revise_stages.Stop):
        async for event in revise_stages.Rewrite().run(bundle, Context()):
            events.append(event)
    assert bundle.document.sections[1] == table
    said = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert said.startswith(said_as)


def test_a_figure_written_without_its_comma_is_still_shown():
    # 「Per 1,000 docs」 cut to 「Cost per 1000」 on another slide (reverify-x EN deck)
    old = [{"layout": "bullets", "title": "Cost", "bullets": ["Per 1,000 docs", "$4 vs $38"]}]
    new = [{"layout": "table", "title": "Core", "rows": [["Criteria", "Local", "Cloud"],
                                                          ["Cost per 1000", "$4", "$38"]]}]
    assert deck.lost_across(old, new) == []
    assert deck.lost_across(old, [{"layout": "bullets",
                                   "bullets": ["Cost per 1k: $4 / $38"]}]) == []
    assert deck.lost_across(old, [{"layout": "bullets", "bullets": ["$4 vs $38"]}]) == ["1,000"]
    started = time.monotonic()
    deck.amounts_in("1" * 50000 + " " * 50000)
    assert time.monotonic() - started < 0.5


#: a figure and the caveat the next sentence puts on it (reverify-x T1)
HEDGED = ("대성유통은 5% 인하를 요구했으며, 경쟁사 A사가 동일 품목에 4% 낮은 단가를 제시한 것으로 "
          "파악됩니다. 다만 해당 경쟁사 단가는 확정된 사실이 아닙니다.")


def test_a_shortening_that_drops_a_figures_caveat_names_it():
    # the caveat kept beside its figure: nothing lost
    assert revise.lost_facts(HEDGED, "대성유통은 5% 인하를 요구했고, A사의 4% 낮은 단가는 확정되지 "
                             "않았습니다.") == []
    assert revise.lost_facts(HEDGED, "대성유통은 5% 인하를 요구했습니다. A사가 4% 낮은 단가를 "
                             "제시했습니다. 이는 미확인 정보입니다.") == []
    # the figure kept, its caveat gone: the mark is named
    assert revise.lost_facts(
        HEDGED, "대성유통은 5% 인하를, A사는 4% 낮은 단가를 제시했습니다.") == [
        "A사 4%의 「확정 아님」 표시"]
    # the name gone with its caveat: the loss says it was the unconfirmed one
    assert revise.lost_facts(HEDGED, "대성유통은 5% 인하를 요구했고 상한선은 4%입니다.") == [
        "A사(확정 아님)"]
    faults = revise.shortening_faults(HEDGED, "대성유통은 5%, A사는 4% 낮은 단가를 냈습니다.",
                                      "2절을 줄여줘")
    assert "A사 4%의 「확정 아님」 표시" in faults[0]
    # a hedge that follows a sentence with nothing of its own to point back at is no caveat
    assert revise.lost_facts("A사가 4%를 제시했습니다. 물류팀 확인이 필요합니다.",
                             "A사가 4%를 제시했습니다.") == []
    started = time.monotonic()
    revise.lost_facts("확정" + " " * 50000 + "아님", "확정" + " " * 50000)
    assert time.monotonic() - started < 0.5
