"""A slide asked only into another shape (「4장을 번호 목록으로 바꿔줘」, 「Can you make slide 3
a table?」) is put into it by code from its own content, title and figures kept; a shape the
content does not go into is said, the slide left as it was; figures lost in a rewrite are
read whole on both sides (「3분기」, not 「3분」)."""

from __future__ import annotations

import json
import time

import pytest

from app.pipeline import revise_stages, save_stages
from app.pipeline.stage import Context
from app.services import deck, revise

TREND = {"id": "q", "layout": "chart", "title": "분기별 성장 추이 분석",
         "chart": {"kind": "bar", "unit": "억 원", "series": [
             {"name": "매출", "values": [114.0, 128.0, 140.0]}],
             "categories": ["전년 3분기", "올해 3분기", "4분기 목표"]},
         "notes": ("차트를 보시면 114억 원에서 128억 원으로 성장했습니다. "
                   "4분기에는 140억 원이 목표입니다.")}
#: the rewrite that put other facts under the title (reverify-n)
DRIFTED = {"id": "q", "layout": "numbered", "title": "분기별 성장 추이 분석",
           "items": [["매출", "128억, 전년 동기 대비 +12%"], ["영업이익률", "7.5%"],
                     ["신규 거래처", "3곳 계약 체결"], ["출고 지연", "4일"]]}
COSTS = {"id": "c", "layout": "bullets", "title": "Cost and Latency at 1,000 Documents",
         "bullets": ["Cost: $4 vs $38", "Latency: 9s vs 14s", "Local wins on both"]}
LINED = {**COSTS, "bullets": ["Metric – $ / 1,000 docs – Latency", "Local 27B – $4 – 9s",
                              "Frontier Cloud – $38 – 14s"]}
GAP = {"id": "g", "layout": "bullets", "title": "The Long-Context Quality Gap",
       "bullets": ["Above 20 pages, local failed often", "Local success rate was 64 percent",
                   "Cloud success rate reached 91 percent", "27 point gap on long context"]}


@pytest.mark.parametrize("words, only", [
    ("4장을 번호 목록으로 바꿔줘", True), ("Can you make slide 3 a table?", True),
    ("Make slide 3 a table", True), ("슬라이드 4를 표로 만들어 주세요", True),
    ("Change slide 4 so it shows the three routing rules as a numbered list.", False),
    ("3장을 표로 바꾸고 비용 항목을 추가해줘", False), ("Make the table smaller", False),
])
def test_only_an_ask_for_another_shape_is_a_change_of_form(words, only):
    assert deck.form_only(words) is only


def test_a_chart_asked_as_a_numbered_list_lists_its_own_points_under_its_title():
    shaped = deck.reshaped(TREND, "numbered")
    assert shaped["layout"] == "numbered" and shaped["title"] == "분기별 성장 추이 분석"
    assert shaped["items"] == [["전년 3분기", "114억 원"], ["올해 3분기", "128억 원"],
                               ["4분기 목표", "140억 원"]]
    assert "chart" not in shaped and "차트" not in shaped["notes"]
    assert deck.lost_figures(TREND, shaped) == []


def test_a_chart_asked_as_a_table_sets_its_values_in_rows():
    shaped = deck.reshaped(TREND, "table")
    assert shaped["rows"] == [["항목", "매출 (억 원)"], ["전년 3분기", "114"],
                              ["올해 3분기", "128"], ["4분기 목표", "140"]]


def test_bullets_whose_fields_line_up_become_a_table_with_their_own_header():
    shaped = deck.reshaped(LINED, "table")
    assert shaped["layout"] == "table" and "bullets" not in shaped
    assert shaped["rows"] == [["Metric", "$ / 1,000 docs", "Latency"],
                              ["Local 27B", "$4", "9s"], ["Frontier Cloud", "$38", "14s"]]


def test_side_by_side_values_no_line_names_wait_for_the_writer_then_go_in_by_code():
    assert deck.reshaped(COSTS, "table") is None
    shaped = deck.reshaped(COSTS, "table", unnamed=True)
    assert shaped["rows"] == [["Item", "", ""], ["Cost", "$4", "$38"],
                              ["Latency", "9s", "14s"]]
    # the point outside the columns is said, not lost
    assert shaped["notes"].startswith("Local wins on both.")


def test_points_that_share_no_fields_are_not_forced_into_a_table():
    assert deck.reshaped(GAP, "table", unnamed=True) is None


def test_figures_are_read_whole_on_both_sides():
    assert deck.lost_figures(TREND, DRIFTED, "4장을 번호 목록으로 바꿔줘") == [
        "3분기", "4분기", "114억", "140억"]
    assert [said for *_, said in deck.figures_in("4분기에는 128억원, 10/16(목), 3개월간")] == [
        "4분기", "128억", "10/16", "3개월"]
    numbered = {"layout": "numbered", "items": [
        ["27B local model", "82% of frontier"], ["Above 20 pages", "64% vs 91%"]]}
    assert deck.lost_figures(GAP, numbered, "") == ["27 point"]
    started = time.monotonic()
    deck.figures_in("1" + " " * 50000 + "가")
    assert time.monotonic() - started < 0.5


def _bundle(typed: str, slides: list[dict]):
    return revise_stages.bundle_for(
        request="발표 자료", instruction=typed, model={"id": "m"}, planner=None, api_key="k",
        is_deck=True, title="발표", data={}, parts=[dict(s) for s in slides], material=[],
        plan=revise.Plan(scope="parts", targets=[1], note=typed), format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})


SLIDES = [{"id": "t", "layout": "title", "title": "발표"}, TREND]


@pytest.mark.asyncio
async def test_a_numbered_list_asked_of_a_chart_is_made_without_the_writer(monkeypatch):
    async def rewrite_slide(**_kwargs):
        raise AssertionError("a change of form is not rewritten")

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    bundle = _bundle("2장을 번호 목록으로 바꿔줘", SLIDES)
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    doc = bundle.document
    assert doc.sections[1]["items"][0] == ["전년 3분기", "114억 원"]
    assert doc.unmet == [] and doc.dropped == []
    assert save_stages.revision_reply(doc, None, is_deck=True) == "2장을 고쳤습니다."


@pytest.mark.asyncio
async def test_a_table_written_out_as_lines_is_put_into_a_table(monkeypatch):
    async def rewrite_slide(**kwargs):
        assert "make up no item" in kwargs["note"]
        return dict(LINED), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    bundle = _bundle("Can you make slide 2 a table?", [SLIDES[0], COSTS])
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    doc = bundle.document
    assert doc.sections[1]["layout"] == "table" and doc.unmet == []
    assert save_stages.revision_reply(doc, None, is_deck=True, english=True) == (
        "Revised slide 2.")


@pytest.mark.asyncio
async def test_a_shape_that_does_not_come_leaves_the_slide_and_says_so(monkeypatch):
    async def rewrite_slide(**_kwargs):
        return {**GAP, "bullets": ["Long context – fails"]}, {"inputTokens": 1,
                                                              "outputTokens": 1}

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    bundle = _bundle("Make slide 2 a table", [SLIDES[0], GAP])
    bundle.plan.order = [1]
    events = []
    with pytest.raises(revise_stages.Stop):
        async for event in revise_stages.Rewrite().run(bundle, Context()):
            events.append(event)
    assert bundle.document.sections[1] == GAP
    said = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert said.startswith("Nothing was changed: slide 2 as a table could not be made")


def test_long_input_is_read_in_linear_time():
    started = time.monotonic()
    deck.form_only("3장을 표로" + " " * 50000 + "바꿔줘")
    deck.reshaped({"layout": "bullets", "bullets": ["a" + " " * 50000 + ": 1 vs 2"] * 2}, "table")
    assert time.monotonic() - started < 0.5


CLOSING = {"id": "z", "layout": "closing", "title": "핵심 내용 및 결의 사항",
           "body": "여러분의 결정을 부탁드립니다.",
           "bullets": ["3분기 매출 128억 원, 영업이익률 7.5% 달성",
                       "4분기 매출 140억 원, 출고 지연 0일 목표",
                       "영남 물류 안정화 예산 1.5억 원 승인 요청"]}
DELAY = {"id": "y", "layout": "bullets", "title": "출고 지연 원인",
         "bullets": ["영남 물류센터 이전으로 출고 업무 중단", "이전 기간 4일 간 출고 지연 발생",
                     "신규 거래처 3곳 확보에도 불구하고 품질 리스크"]}


@pytest.mark.parametrize("words, count", [
    ("9장 항목을 하나로 줄여줘", 1), ("6장 항목을 두 개로 줄여줘", 2), ("3개로 묶어줘", 3),
    ("Cut slide 6 to two bullets", 2), ("6장을 줄여줘", 0),
])
def test_the_item_count_an_ask_names_is_read(words, count):
    assert deck.items_asked(words) == count


def test_items_merged_into_one_keep_their_facts():
    emptied = {**CLOSING, "body": "감사합니다"}
    emptied.pop("bullets")
    joined = deck.merged_points(CLOSING, emptied, 1)
    assert len(joined["bullets"]) == 1 and deck.lost_figures(CLOSING, joined) == []
    assert deck.merged_points(CLOSING, CLOSING, 1) is None
    started = time.monotonic()
    deck.items_asked("두" + " " * 50000 + "개로 줄여")
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_a_slide_cut_to_fewer_items_says_the_figures_it_dropped(monkeypatch):
    async def rewrite_slide(**_kwargs):
        return {**DELAY, "bullets": ["영남 물류센터 이전", "출고 지연 발생"]}, {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    bundle = _bundle("2장 항목을 두 개로 줄여줘", [SLIDES[0], DELAY])
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    assert bundle.document.dropped == ["2장 4일, 3곳"]
    assert save_stages.revision_reply(bundle.document, None, is_deck=True) == (
        "2장을 고쳤습니다. 2장에서 4일, 3곳이 빠졌습니다. 필요하면 다시 넣어 달라고 해 주세요.")


@pytest.mark.asyncio
async def test_items_asked_into_one_and_answered_with_none_are_joined(monkeypatch):
    async def rewrite_slide(**_kwargs):
        return {k: v for k, v in CLOSING.items() if k != "bullets"} | {"body": "감사합니다"}, {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    bundle = _bundle("2장 항목을 하나로 줄여줘", [SLIDES[0], CLOSING])
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    slide = bundle.document.sections[1]
    assert len(slide["bullets"]) == 1 and "128억" in slide["bullets"][0]
    assert bundle.document.dropped == []


def test_a_deck_cut_to_fewer_slides_says_the_figures_it_no_longer_shows():
    from app.routers import sessions

    # a count with what it counts (「신규 거래처 3곳」), not a bare 「3곳」
    assert deck.lost_across([TREND, DELAY], [TREND]) == ["4일", "신규 거래처 3곳"]
    assert sessions._restructured_said([TREND, DELAY], [TREND], english=False) == (
        "1장짜리 슬라이드를 만들었습니다. 고치면서 4일, 신규 거래처 3곳이 슬라이드에서 "
        "빠졌습니다. "
        "필요하면 다시 넣어 달라고 해 주세요.")
    assert sessions._restructured_said([], [TREND], english=False) == (
        "1장짜리 슬라이드를 만들었습니다.")


#: reverify-u kd2: a pie and a line chart the shrink took off; kd1: a figure kept only in notes
PIE = {"id": "p", "layout": "chart", "title": "지역별 매출 구성 현황",
       "chart": {"kind": "pie", "unit": "%", "categories": ["수도권", "영남", "호남"],
                 "series": [{"name": "매출 구성비", "values": [55.5, 25.8, 10.9]}]}}
MARGIN = {"id": "m", "layout": "chart", "title": "이익률 추이와 수익성 변화",
          "chart": {"kind": "line", "unit": "%", "categories": ["전분기", "3분기"],
                    "series": [{"name": "이익률", "values": [6.8, 7.5]}]},
          "bullets": ["영업이익률 7.5%"]}
NOTED = {"id": "n", "layout": "bullets", "title": "3분기 주요 지표", "bullets": ["전분기 6.8%"],
         "notes": "3분기 매출은 128억 원, 영업이익률은 7.5%입니다."}


def test_a_shrink_names_the_charts_it_took_off_and_counts_no_figure_kept_only_in_notes():
    from app.routers import sessions

    kept = {"id": "k", "layout": "bullets", "title": "요약", "bullets": ["3분기 매출 128억 원"]}
    assert deck.lost_blocks([PIE, MARGIN], [NOTED]) == [
        "「지역별 매출 구성 현황」 원형 차트", "「이익률 추이와 수익성 변화」 선 차트"]
    assert deck.lost_blocks([PIE], [{**NOTED, "chart": PIE["chart"]}]) == []
    # values only the gone chart plotted are said with it, not one by one
    plotted = deck.charted_only([PIE, MARGIN], [NOTED])
    assert {"55.5%", "25.8%", "10.9%"} <= plotted and "7.5%" not in plotted
    assert sessions._restructured_said([kept, PIE, MARGIN], [NOTED], english=False) == (
        "1장짜리 슬라이드를 만들었습니다. 고치면서 128억, 7.5%와 「지역별 매출 구성 현황」 원형 "
        "차트, 「이익률 추이와 수익성 변화」 선 차트가 슬라이드에서 빠졌습니다. 필요하면 다시 넣어 "
        "달라고 해 주세요.")


#: reverify-u ed1: an English deck cut to three slides
EN_TABLE = {"id": "t", "layout": "table", "title": "Performance and Cost Comparison",
            "rows": [["Criterion", "27B Local", "Frontier API"], ["Cost / 1k docs", "$4", "$38"],
                     ["Latency", "9s", "14s"], ["Long-ctx (20+ pg)", "64%", "91%"]]}
EN_LEFT = {"id": "l", "layout": "bullets", "title": "Benchmark",
           "bullets": ["27B: 64% vs 91% above 20 pages"]}


def test_an_english_shrink_is_said_in_english_with_whole_values_and_the_table_named():
    from app.routers import sessions

    assert deck.lost_across([EN_TABLE], [EN_LEFT]) == ["1k docs", "$4", "$38", "9s", "14s"]
    assert sessions._restructured_said([EN_TABLE], [EN_LEFT], english=True) == (
        "Made a 1-slide deck. No slide shows these any more: 1k docs, $4, $38, 9s, 14s; the "
        "table on 'Performance and Cost Comparison'. Ask again to put them back if they belong "
        "there.")
    started = time.monotonic()
    deck._whole_figure(("1", "k", "1k"), ["1k" + " " * 50000 + "docs"], True)
    assert time.monotonic() - started < 0.5


def test_a_timeline_put_back_takes_no_slide_whose_figures_it_would_drop():
    old = [{"id": "t", "layout": "timeline", "title": "4분기 목표 및 물류 안정화 전략",
            "timeline": [["4분기 시작", "출고 지연 0일 목표"], ["4분기 말", "안정화 완료"]]}]
    new = [{"id": "a", "layout": "metrics", "title": "3분기 주요 지표 및 지역별 매출 구성",
            "metrics": [["128억", "매출"], ["7.5%", "영업이익률"]], "chart": PIE["chart"]},
           {"id": "b", "layout": "roadmap", "title": "영남 물류 안정화 전략 및 4분기 계획",
            "items": [["4분기 초", "물류 일정 재편"], ["4분기 중", "출고 지연 0일"]]}]
    out = revise.keep_timeline_layout([dict(s) for s in new], old, mentioned="6장으로 줄여줘")
    assert out[0]["layout"] == "metrics" and out[0]["chart"] == PIE["chart"]
    assert out[1]["layout"] == "timeline"


#: reverify-v ed1: an English 8-slide deck cut to three
EN_METHOD = {"id": "b", "layout": "bullets", "title": "Benchmark Design and Evaluation Method",
             "bullets": ["Workload: 40 document drafting tasks",
                         "Reviewers: 3 human evaluators per task"],
             "diagram": {"figure": "flow", "caption": "Evaluation Flow",
                         "source": 'flowchart LR\n  l0s0("40 Tasks") --> l0s1("27B Model")'
                                   ' --> l0s2("3 Reviewers")'}}
EN_GAP = {"id": "g", "layout": "metrics", "title": "The Long-Context Quality Gap",
          "metrics": [["64%", "Local success rate"], ["91%", "Cloud success rate"],
                      ["20+", "Page threshold"]]}
EN_AGENDA = {"id": "a", "layout": "agenda", "title": "목차", "bullets": ["Benchmark design"]}
EN_RESULTS = {"id": "r", "layout": "table", "title": "Benchmark Results",
              "rows": [["Metric", "27B Local", "Frontier Cloud"], ["Average Latency", "9s", "14s"],
                       ["Long-Context Quality", "64%", "91%"], ["Avg Latency", "9s", "14s"]]}


def test_an_english_shrink_names_each_lost_value_with_what_it_counts():
    from app.routers import sessions

    assert deck.lost_across([EN_AGENDA, EN_METHOD, EN_GAP], [EN_RESULTS]) == [
        "40 tasks", "3 reviewers", "20+ page threshold"]
    assert sessions._restructured_said([EN_METHOD, EN_GAP], [EN_RESULTS], english=True) == (
        "Made a 1-slide deck. No slide shows these any more: 40 tasks, 3 reviewers, 20+ page "
        "threshold. Ask again to put them back if they belong there.")
    # a lone count the new deck still shows is not lost; a slide number is no count
    kept = {**EN_RESULTS, "bullets": ["Rated by 3 reviewers"]}
    assert "3 reviewers" not in deck.lost_across([EN_METHOD], [kept])
    assert deck.lost_across([{"layout": "bullets", "title": "Plan", "bullets": ["See slide 3"]}],
                            [EN_RESULTS]) == []
    started = time.monotonic()
    deck.lost_across([{"layout": "bullets", "title": "Plan",
                       "bullets": ["3" + " " * 50000 + "x", "3 a" + " " * 50000]}], [EN_RESULTS])
    deck._counted_noun("3" + " a" * 25000, 1)
    assert time.monotonic() - started < 0.5


def test_an_amount_written_in_two_parts_is_one_figure_lost_or_kept_whole():
    """audit-g H4 A: 「연간 발급 4만 8천 건」 gone from the cut deck was said lost as
    「4만, 8천」."""
    old = [{"layout": "bullets", "title": "핵심 운영 지표",
            "bullets": ["연간 발급 4만 8천 건", "전체 증명서 발급의 31% 차지"]},
           {"layout": "bullets", "title": "예산", "bullets": ["확대 설치 예산 1억 2천만원"]}]
    new = [{"layout": "bullets", "title": "성과", "bullets": ["전체 증명서 발급의 31% 차지",
                                                          "예산 120,000,000원"]}]
    assert deck.lost_across(old, new) == ["발급 4만 8천 건"]
    assert deck.lost_across(old, [*new, {"layout": "bullets", "title": "발급",
                                         "bullets": ["48,000건 발급"]}]) == []
    assert deck.figures_in("38억 5천만 원") == [("3850000000", "원", "38억 5천만 원")]
    started = time.monotonic()
    deck.figures_in("4만" + " " * 50000 + "8천")
    assert time.monotonic() - started < 0.5


def test_a_restated_figure_goes_beside_the_figure_it_belongs_to_not_onto_an_unrelated_slide():
    """reverify-v kd2: 「영업이익 9.6억(이익률 7.5%, 전분기 6.8%)」 the deck left out landed as
    bullets on 「영남 물류 지연 원인 및 현황」."""
    request = ("임원회의 보고 슬라이드. 수치: 매출 128억(전년 동기 114억, +12%), 영업이익 9.6억"
               "(이익률 7.5%, 전분기 6.8%), 출고 지연 4일(영남 물류센터 이전 탓).")
    slides = [
        {"layout": "title", "title": "3분기 실적"},
        {"layout": "metrics", "title": "3분기 핵심 지표",
         "metrics": [["128억", "매출"], ["114억", "전년 동기"], ["12%", "매출 성장률"],
                     ["9.6억", "영업이익"]]},
        {"layout": "bullets", "title": "영남 물류 지연 원인 및 현황",
         "bullets": ["물류센터 이전으로 출고 지연 4일"]},
    ]
    assert deck.restate_missing_facts(slides, request) == [1]
    assert slides[1]["metrics"][3] == ["9.6억", "영업이익 (이익률 7.5%, 전분기 6.8%)"]
    assert slides[2]["bullets"] == ["물류센터 이전으로 출고 지연 4일"]


def test_a_restated_figure_from_a_parenthesis_keeps_the_term_it_qualifies():
    """audit-g H5s: 「93% of target」 from 「revenue $9.8M (target $10.5M, 93% of target)」 went
    alone onto the permit-delay slide, which shares only 「target」."""
    request = ("Create a 6-slide English presentation. Numbers: revenue $9.8M (target $10.5M, "
               "93% of target), gross margin 21%, permit delays in two counties added 11 days "
               "on average.")
    kpis = {"layout": "kpi-grid", "title": "Q3 KPIs",
            "metrics": [["412", "residential installs"], ["9.8", "revenue ($M)"],
                        ["21%", "gross margin"]]}
    delays = {"layout": "bullets", "title": "Permit Delays",
              "bullets": ["Two counties added 11 days on average",
                          "Permit delay hit the 10.5 install target"]}
    slides = [{"layout": "title", "title": "Q3 2026"}, kpis, delays]
    assert deck.restate_missing_facts(slides, request) == [1]
    assert kpis["metrics"][1] == ["9.8", "revenue ($M, target $10.5M, 93% of target)"]
    assert len(delays["bullets"]) == 2
    slides = [{"layout": "title", "title": "Q3 2026"},
              {"layout": "bullets", "title": "Revenue",
               "bullets": ["Revenue landed at $9.8M", "Gross margin 21%"]}]
    deck.restate_missing_facts(slides, request)
    assert slides[1]["bullets"][-1] == "revenue $9.8M (target $10.5M, 93% of target)"


#: reverify-w kd2: the ask slide before the shrink and the statement that took its place
ASK_SLIDE = {"id": "a", "layout": "cards-3", "title": "결재 요청: 영남 물류 안정화 예산 1.5억 승인",
             "items": [["대상", "영남 물류센터 안정화 작업"], ["목적", "출고 지연 0일 달성 지원"]]}
BUDGET = {"id": "b", "layout": "statement", "title": "1.5억 원 예산",
          "body": "영남 물류를 정상화해 4분기 출고 지연을 제로로 만든다"}
COVER = {"id": "c", "layout": "title", "title": "2026년 3분기 사업 실적 및 4분기 계획"}


def test_a_shrink_that_lost_the_deck_s_ask_puts_it_back_on_the_slide_with_its_figure():
    out = revise.keep_ask([dict(COVER), dict(TREND), dict(BUDGET)], [COVER, TREND, ASK_SLIDE],
                          mentioned="슬라이드를 6장으로 줄여줘")
    assert out[2]["title"] == ASK_SLIDE["title"] and out[2]["body"] == BUDGET["body"]
    assert out[1] == TREND
    # a slide with points takes it as a point; a deck still asking, or an ask the person spoke
    # of, stays as written
    closing = {"id": "z", "layout": "closing", "title": "4분기 목표", "bullets": ["매출 140억 원"]}
    out = revise.keep_ask([dict(COVER), dict(closing)], [COVER, ASK_SLIDE], mentioned="")
    assert out[1]["bullets"] == ["매출 140억 원", ASK_SLIDE["title"]]
    asked = {**BUDGET, "body": "예산 1.5억 원 승인을 요청드립니다"}
    assert revise.keep_ask([dict(COVER), dict(asked)], [COVER, ASK_SLIDE], mentioned="") == [
        COVER, asked]
    assert revise.keep_ask([dict(COVER), dict(BUDGET)], [COVER, ASK_SLIDE],
                           mentioned="승인 요청 장은 빼줘")[1] == BUDGET
    started = time.monotonic()
    revise._DECK_ASK.search("요청" + " " * 50000 + "x")
    revise._ORDER_ASK.search("맨" + " " * 50000 + "x")
    assert time.monotonic() - started < 0.5


#: reverify-w kd1: the shrink put the Q3 summary after the Q4 plan
OLD_KD1 = [
    COVER,
    {"id": "1", "layout": "numbered", "title": "3분기 주요 성과 지표",
     "items": [["매출", "128억 원"], ["영업이익", "9.6억 원"], ["영업이익률", "7.5%"]]},
    {"id": "2", "layout": "table", "title": "분기별 재무 실적 비교",
     "rows": [["구분", "Q3 실적", "전년 동기", "YoY"], ["매출", "128억", "114억", "+12%"],
              ["영업이익", "9.6억", "-", "-"], ["영업이익률", "7.5%", "-", "-"]]},
    {"id": "3", "layout": "bands", "title": "3분기 운영 이슈 및 대응",
     "bands": [["현황", "출고 지연 4일, 영남 매출 33억 원 차질"], ["대응", "출고 지연 0일 목표"]]},
    {"id": "4", "layout": "two-column", "title": "4분기 목표 및 영남 물류 안정화",
     "bullets": ["출고 지연 0일 달성", "예산 1.5억 원 승인 요청", "신규 거래처 3곳 유지 관리"]},
    {"id": "5", "layout": "closing", "title": "3분기 핵심 요약 및 4분기 목표",
     "bullets": ["매출 128억, 영업이익률 7.5%", "4분기 매출 140억 목표"]},
]
NEW_KD1 = [
    COVER, OLD_KD1[3],
    {"id": "p", "layout": "bullets", "title": "4분기 목표 및 물류 안정화",
     "bullets": ["출고 지연 0일 기준 운영", "신규 거래처 3곳 유지", "안정화 예산 1.5억 원"]},
    {"id": "s", "layout": "metrics", "title": "3분기 핵심 요약",
     "metrics": [["128억", "3분기 총 매출"], ["9.6억", "영업이익 (이익률 7.5%)"], ["+12%", "YoY"]]},
    {"id": "z", "layout": "closing", "title": "4분기 목표 및 승인 요청",
     "bullets": ["4분기 매출 목표 140억 원", "물류 안정화 예산 1.5억 원 승인"]},
]


def test_a_summary_the_shrink_put_after_later_slides_moves_up_to_where_its_figures_were():
    out = revise.keep_order(list(NEW_KD1), OLD_KD1, mentioned="6장으로 줄여줘")
    assert [s["id"] for s in out] == ["c" if s is COVER else s["id"] for s in [
        COVER, NEW_KD1[3], NEW_KD1[1], NEW_KD1[2], NEW_KD1[4]]]
    # an order the person asked for, or a summary already ahead, stays
    assert revise.keep_order(list(NEW_KD1), OLD_KD1, mentioned="요약을 맨 뒤로 옮겨줘") == NEW_KD1
    ahead = [COVER, NEW_KD1[3], NEW_KD1[1], NEW_KD1[2], NEW_KD1[4]]
    assert revise.keep_order(list(ahead), OLD_KD1, mentioned="") == ahead


#: reverify-w ed1: the shrink dropped the mark on 95% and wrote a cost point anew
EN_OLD = [
    {"id": "t", "layout": "table", "title": "Quality and Cost Head-to-Head",
     "rows": [["Metric", "27B Local", "Frontier Cloud"], ["Drafting score", "82%",
              "95% (needs checking)"], ["Cost per 1,000", "$4", "$38"]]},
    {"id": "k", "layout": "takeaways", "title": "Recommendation and Key Takeaways",
     "items": [["Local wins on cost: $4 vs $38 per 1,000", ""],
               ["Hybrid routing is the optimal path", ""]]},
]
EN_NEW = [
    {"id": "t", "layout": "table", "title": "Benchmark Results",
     "rows": [["Metric", "27B Local", "Cloud API"], ["Drafting score", "82%", "95%"],
              ["Cost / 1,000 docs", "$4", "$38"]]},
    {"id": "h", "layout": "bullets", "title": "Hybrid Routing Recommendation",
     "bullets": ["Route short drafts locally", "Blend $4 and $38 costs"]},
]


def test_a_shrink_keeps_the_old_deck_s_marks_and_its_wording_of_a_figure_pair():
    out = revise.carry_marks(json.loads(json.dumps(EN_NEW)), EN_OLD)
    assert out[0]["rows"][1] == ["Drafting score", "82%", "95% (needs checking)"]
    assert out[0]["rows"][2] == EN_NEW[0]["rows"][2]
    # marked once is enough: a mark already there is not doubled, a figure shown bare in the
    # old deck too is left alone
    assert revise.carry_marks(out, EN_OLD) == out
    bare = [*EN_OLD, {"id": "x", "layout": "bullets", "title": "x", "bullets": ["95% cloud"]}]
    assert revise.carry_marks(json.loads(json.dumps(EN_NEW)), bare) == EN_NEW
    ko = [{"id": "m", "layout": "bullets", "title": "x", "bullets": ["예산 1.5억 (확인 필요)"]}]
    got = revise.carry_marks([{"id": "m", "layout": "bullets", "title": "x",
                               "bullets": ["예산 1.5억 원 집행"]}], ko)
    assert got[0]["bullets"] == ["예산 1.5억 원 (확인 필요) 집행"]
    out = revise.keep_phrasing(json.loads(json.dumps(EN_NEW)), EN_OLD)
    assert out[1]["bullets"] == ["Route short drafts locally",
                                 "Local wins on cost: $4 vs $38 per 1,000"]
    # the old words kept, or a long sentence of the writer's own: stays
    same = [{**EN_NEW[1], "bullets": ["Cost: $4 vs $38 per 1,000 docs"]}]
    assert revise.keep_phrasing(json.loads(json.dumps(same)), EN_OLD) == same
    # audit-g H4 B: the writer's cut of the old point stays
    old_point = [{"id": "o", "layout": "bullets", "title": "과제",
                  "bullets": ["고령층 이용률: 65세 이상 이용률이 12%로 낮아 안내와 "
                              "접근성 보완이 필요함"]}]
    cut = [{"id": "o", "layout": "bullets", "title": "과제",
            "bullets": ["65세 이상 이용률 12%로 낮음"]}]
    assert revise.keep_phrasing(json.loads(json.dumps(cut)), old_point) == cut
    started = time.monotonic()
    revise._MARKED_FIGURE.search("1" + " " * 50000 + "(x")
    revise.carry_marks([{"bullets": ["95%" + " " * 50000]}], EN_OLD)
    assert time.monotonic() - started < 0.5


def test_a_dangling_unit_is_cut_and_a_guard_blank_stays_in_sight():
    slide = {"id": "b", "layout": "bullets", "title": "분기별 매출 및 이익률 추이",
             "bullets": ["매출 실적: 128억원/%", "이익률 실적: [수치]", "이익률 7.5%"]}
    assert deck.settle_blanks([slide]) == 1
    assert slide["bullets"] == ["매출 실적: 128억원", "이익률 실적: [수치]", "이익률 7.5%"]
    kept = {"id": "k", "layout": "bullets", "title": "x", "bullets": ["3%/년 성장"]}
    assert deck.settle_blanks([kept]) == 0
    started = time.monotonic()
    deck._DANGLING_UNIT.sub("", "1" + " " * 50000 + "/")
    assert time.monotonic() - started < 0.5
