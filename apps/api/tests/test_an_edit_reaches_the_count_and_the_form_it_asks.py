"""An edit reaches the count and form it asks: 「Shrink to 3 slides」 states the count however
the planner put it, an English deck is not judged Korean by the code's own conditions, a
sentence count leaves out what only points at the table and dates nothing anew, a part that
already has the count stays, 「두 개로 나눠줘」 makes two items, a numbered edit leaves an
open outline card, and figures and tables are read as shown (「Cost/1k」, a table twice)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.models.chat import SessionKind
from app.pipeline import revise_stages, save_stages
from app.pipeline.stage import Context
from app.services import deck, revise, weekdays

EN_REQUEST = ("Create an 8-slide English presentation for a conference talk: 'Can small "
              "on-premise language models replace cloud APIs?' Cost per 1,000 documents "
              "was $4 vs $38.")


def _restructured(note: str) -> str:
    plan = revise.count_change(revise.Plan(scope="outline", note=note), ["a"] * 8,
                               "Shrink to 3 slides")
    return revise.without_counts(EN_REQUEST) + "\n\n덧붙인 조건:\n- " + plan.note


def test_a_shrink_states_its_count_however_the_planner_noted_it():
    # the planner already chose a new outline, its note naming two counts or none
    assert deck.requested_slides(_restructured("현재 8장을 3장으로 줄인다")) == 3
    assert deck.requested_slides(_restructured("핵심만 남기고 줄인다")) == 3
    assert "8-slide" not in revise.without_counts(EN_REQUEST)
    assert revise.without_counts("Make it in 10 slides please") == "Make it in please"


def test_an_english_deck_is_not_judged_korean_by_the_codes_conditions():
    plan = [{"title": "Small Models vs Cloud APIs"}, {"title": "Benchmark Results"},
            {"title": "Key Takeaways"}]
    assert deck.sane_outline(plan, _restructured("3장으로 줄인다"))
    assert not deck.sane_outline(plan, "발표 자료를 만들어줘")


TABLED = ("대성유통은 월요일 1차 미팅에서 월 발주 예상액 3,200만원을 확인하고 목요일에 견적서를 "
          "발송했습니다 [1].\n\n한솔마트 재고 클레임 2건은 처리를 완료했습니다. 3분기 매출은 "
          "41.2억원이었습니다.\n\n본 주 핵심 성과를 요약하면 표와 같습니다(표 1).\n\n"
          "**표 1. 주요 업무 실적**\n\n| 사업 | 상태 |\n|---|---|\n| 대성유통 | 진행 중 |")


def test_a_sentence_that_only_points_at_the_table_is_the_tables():
    assert revise.sentence_count(TABLED) == 4
    folded = revise.fold_table_pointers(TABLED, 3)
    assert revise.sentence_count(folded) == 3
    assert "41.2억원이었습니다(표 1)." in folded and "표와 같습니다" not in folded
    assert "| 대성유통 | 진행 중 |" in folded
    # at the count asked, or a sentence with its own figure: left as written
    assert revise.fold_table_pointers(TABLED, 4) == TABLED
    figured = TABLED.replace("요약하면 표와 같습니다", "요약하면 3건이 표와 같습니다")
    assert revise.sentence_count(revise.fold_table_pointers(figured, 3)) == 4


def test_a_shortening_dates_nothing_anew():
    old = "월요일 1차 미팅 후 목요일에는 견적서를 발송했습니다."
    new = "10월 12일 월요일 1차 미팅 후 10월 16일 목요일에 견적서를 발송했습니다."
    given = "2026년 10월 3주차(10/12~10/16)"
    # the numbers are in the material, the days are not the section's
    assert revise.added_facts(old, new, given) == ["10월 12일", "10월 16일"]
    assert revise.without_added_dates(old, new) == (
        "월요일 1차 미팅 후 목요일에 견적서를 발송했습니다.")
    # a day the section had stays; a weekday the section never said is left for the checks
    assert revise.without_added_dates("10월 16일 마감", "10월 16일 금요일 마감") == (
        "10월 16일 금요일 마감")
    assert revise.without_added_dates(old, "10월 17일 토요일 발송") == "10월 17일 토요일 발송"
    # a weekday written beside its date is the calendar's
    assert weekdays.fix_weekdays("10월 16일 목요일 발송", context=given)[0] == (
        "10월 16일 금요일 발송")


TWO = "대성유통과의 단가 협상(5% 인하 요구)은 남아 있습니다. A사의 4% 낮은 단가는 확정이 아닙니다."
ONE = "대성유통 단가 협상(5% 인하 요구)과 A사의 4% 낮은 단가 정보가 남아 있습니다."


async def _two_sentences(monkeypatch, answers, before=TWO):
    notes: list[str] = []
    given = iter(answers)

    async def rewrite_section(**kwargs):
        notes.append(kwargs["note"])
        return next(given), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    ask = "2절을 두 문장으로 줄여줘"
    parts = [{"id": "s0", "heading": "성과", "content": "매출 41.2억 원입니다.",
              "format": "markdown"},
             {"id": "s1", "heading": "현황", "content": before, "format": "markdown"}]
    bundle = revise_stages.bundle_for(
        request="주간 업무 보고서", instruction=ask, model={"id": "m"}, planner=None,
        api_key="k", is_deck=False, title="주간 보고", data={}, parts=parts, material=[],
        plan=revise.Plan(scope="parts", targets=[1], note=ask, clauses={1: [ask]}),
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
async def test_a_part_that_already_has_the_count_stays_and_that_is_said(monkeypatch):
    doc, notes, events = await _two_sentences(monkeypatch, [ONE, ONE])
    assert len(notes) == 2 and "exactly 2 sentences" in notes[1]
    assert doc.sections[1]["content"] == TWO and doc.already == [(2, 2)]
    said = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert said == "2절은 이미 2문장이어서 그대로 두었습니다."
    assert save_stages.already_said([(2, 2)], english=True) == (
        "Section 2 already has 2 sentences, so it was kept as it was.")
    # a rewrite at the count stands
    doc, _, _ = await _two_sentences(monkeypatch, [TWO.replace("남아 있습니다", "남았습니다")])
    assert doc.already == [] and "남았습니다" in doc.sections[1]["content"]


def test_split_into_two_makes_two_items_from_the_joined_points():
    joined = {"id": "x", "layout": "bullets", "title": "실적",
              "bullets": ["매출 128억 원 · 영업이익 9.6억 원 · 이익률 7.5% · 수도권 71억 원 · "
                          "영남 33억 원 · 호남·기타 24억 원"]}
    assert deck.items_asked("3장 항목을 두 개로 나눠줘") == 2
    split = deck.joined_items(joined, 2)
    assert split["bullets"] == ["매출 128억 원 · 영업이익 9.6억 원 · 이익률 7.5%",
                                "수도권 71억 원 · 영남 33억 원 · 호남·기타 24억 원"]
    # more items than asked are joined into the count
    six = {**joined, "bullets": [f"항목 {i}" for i in range(6)]}
    assert len(deck.joined_items(six, 2)["bullets"]) == 2
    # one point that does not come apart: left to the writer
    assert deck.joined_items({**joined, "bullets": ["매출 128억 원"]}, 2) is None


def test_a_numbered_edit_leaves_an_open_outline_card():
    from app.routers import sessions

    def session(stage):
        return SimpleNamespace(pending={"stage": stage}, artifact_id="a1", kind=SessionKind.slides)

    def payload(text):
        return SimpleNamespace(content=text, approve=False, answers=None)

    assert sessions._sets_card_aside(session("plan"), payload("3장을 번호 목록으로 바꿔줘"))
    # a change of the proposed structure is still a note on it
    assert not sessions._sets_card_aside(session("plan"), payload("4장은 빼고 진행해 줘"))
    assert not sessions._sets_card_aside(session("plan"), payload("투자자용으로 바꿔줘"))
    # a question card: a named part put into a shape, the verb left out, is an edit
    assert sessions._sets_card_aside(session("clarify"), payload("3장을 번호 목록으로"))
    assert sessions._sets_card_aside(session("clarify"), payload("4장 항목을 하나로"))
    assert sessions._sets_card_aside(session("clarify"), payload("3장 수치는 어디서 나온 거야?"))
    # the card's own answer, naming no part, stays its answer
    assert not sessions._sets_card_aside(session("clarify"), payload("투자자 대상 10장으로"))
    assert not sessions._sets_card_aside(session("clarify"), payload("표로 정리해서"))


#: reverify-s kd2 slides asked 「N장 항목을 하나로」 with no verb
KD2_SLIDES = [
    {"id": "sl1", "layout": "agenda", "title": "발표 순서",
     "bullets": ["3분기 주요 성과 요약", "매출 및 이익률 전년 동기 비교", "지역별 매출 구성 분석",
                 "영업 이슈: 출고 지연 원인", "4분기 목표 및 개선 계획",
                 "핵심 요청: 영남 물류 안정화 예산"]},
    {"id": "sl3", "layout": "bullets", "title": "매출 및 이익률 전년 동기 비교",
     "bullets": ["3분기 매출 128억 원: 전년 동기 114억 원 대비 14억 원 증가",
                 "매출 성장률 +12% 달성", "이익률 6.8% → 7.5%로 0.7%p 상승"]},
    {"id": "sl3b", "layout": "bullets", "title": "매출 및 이익률 전년 동기 비교",
     "bullets": ["매출 128억 원 (전년 동기 +12%) · 영업이익 9.6억 원 (이익률 7.5%)",
                 "전분기 이익률 6.8% 대비 0.7%p 상승"]},
    {"id": "sl8", "layout": "closing", "title": "경영 의사결정 요약", "body": "질의 환영합니다",
     "bullets": ["3분기 매출 128억 원 달성", "4분기 목표 매출 140억 원",
                 "영남 물류 안정화 예산 1.5억 원"]},
]


@pytest.mark.parametrize("ask", ["4장 항목을 하나로", "9장 항목을 하나로.",
                                 "2장 항목을 하나로 해줘", "4장 항목을 하나로 합쳐줘"])
def test_items_into_one_without_a_verb_join_by_code(ask):
    assert revise.joins_items(ask) and deck.items_asked(ask) == 1
    for slide in KD2_SLIDES:
        joined = deck.joined_items(slide, deck.items_asked(ask))
        assert joined is not None and len(deck.points_of(joined)) == 1, slide["id"]
        assert deck.lost_figures(slide, joined) == []
    # 「두 개로 나눠줘」 still splits; two slides made one is not an item ask
    assert deck.items_asked("3장 항목을 두 개로 나눠줘") == 2
    assert not revise.joins_items("4장과 5장을 하나로")


def test_a_shortening_that_names_its_part_changes_no_fact():
    from app.services import consistency

    assert not consistency.changes_content("2절을 두 문장으로 줄여줘")
    assert not consistency.changes_content("2절을 줄여줘")
    assert consistency.changes_content("2절 매출을 42억으로 고쳐줘")
    assert consistency.changes_content("3절의 마감을 10월 30일로 바꿔줘")


@pytest.mark.asyncio
async def test_the_count_is_read_after_internal_notes_go(monkeypatch):
    # the second sentence names an internal block: scrubbed, so the part has one sentence
    noted = ONE + " 확정 수치 기준입니다."
    doc, notes, events = await _two_sentences(monkeypatch, [noted, noted], before=TWO + " " + ONE)
    assert len(notes) == 2 and "the last version had 1" in notes[1]
    assert doc.counted == [(2, 2, 1)]


@pytest.mark.asyncio
async def test_a_repair_that_undoes_the_count_is_said(monkeypatch):
    bundle = SimpleNamespace(
        plan=SimpleNamespace(order=[1], revision=revise.Plan(
            scope="parts", targets=[1], clauses={1: ["2절을 두 문장으로 줄여줘"]})),
        request=SimpleNamespace(is_deck=False, instruction="2절을 두 문장으로 줄여줘", text="",
                                model="m", api_key="k"),
        document=SimpleNamespace(sections=[{"id": "s0", "content": "가."},
                                           {"id": "s1", "content": TWO}], counted=[]),
        material=SimpleNamespace(document=[]), computed=SimpleNamespace(prepass=None),
        findings=SimpleNamespace(aligned=None))

    async def align(parts, **kwargs):
        return [parts[0], {**parts[1], "content": ONE}], {"type": "step"}, {}

    monkeypatch.setattr(revise_stages.consistency, "align", align)
    monkeypatch.setattr(revise_stages, "_tidy_tables", lambda bundle: [])
    [e async for e in revise_stages.Consistency().run(bundle, Context())]
    assert bundle.document.counted == [(2, 2, 1)]


def test_a_figure_after_a_slash_is_read_and_a_table_twice_is_found():
    old = [{"layout": "table", "rows": [["Metric", "Local"], ["Cost / 1k docs", "$4"]]}]
    new = [{"layout": "table", "rows": [["Metric", "Local"], ["Cost/1k docs", "$4"]]}]
    assert deck.lost_across(old, new) == []
    assert "1/2" not in [s for *_, s in deck.figures_in("1/2 of 3/4")][1:]
    rows = [["Metric", "Local", "Cloud"], ["Score", "82%", "100%"], ["Cost", "$4", "$38"]]
    again = [["Metric", "Local", "Cloud Frontier"], ["Score", "82%", "100%"], ["Cost", "$4", "$38"]]
    assert deck.repeats_table(again, [None, rows])
    assert not deck.repeats_table([["a", "b"], ["Score", "82%", "100%"], ["New", "1", "2"]],
                                  [rows])


def test_the_new_patterns_take_linear_time_on_long_blank_input():
    spaces = " " * 50000
    started = time.monotonic()
    revise.without_counts("8" + spaces + "slides")
    revise.added_facts("", "10" + spaces + "월 16일" + spaces + "목요일", "")
    revise.without_added_dates("목요일", "10월 16일" + spaces + "목요일")
    revise.fold_table_pointers("가나다." + spaces + "표" + spaces + "같습니다.", 0)
    deck.items_asked("두" + spaces + "개로 나눠")
    deck.items_asked("하나로" + spaces + "x")
    revise.joins_items("항목 하나로" + spaces + "x")
    deck.joined_items({"bullets": ["a" + spaces + "· b"]}, 3)
    deck.figures_in("Cost" + spaces + "/" + spaces + "1k")
    assert time.monotonic() - started < 0.5
