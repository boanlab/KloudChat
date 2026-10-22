"""A slide asked only into another shape (「4장을 번호 목록으로 바꿔줘」, 「Can you make slide 3
a table?」) is put into it by code from its own content, title and figures kept; a shape the
content does not go into is said, the slide left as it was; figures lost in a rewrite are
read whole on both sides (「3분기」, not 「3분」)."""

from __future__ import annotations

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

    assert deck.lost_across([TREND, DELAY], [TREND]) == ["4일", "3곳"]
    assert sessions._restructured_said([TREND, DELAY], [TREND], english=False) == (
        "1장짜리 슬라이드를 만들었습니다. 고치면서 4일, 3곳이 슬라이드에서 빠졌습니다. "
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
    deck._whole_figure(("1", "k", "1k"), "1k" + " " * 50000 + "docs")
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


def test_a_closing_with_no_points_names_none():
    slide = {"layout": "closing", "title": "핵심 요약 및 승인 요청 사항"}
    deck.fill_closing(slide, {"bullets": [], "body": ""})
    assert slide["title"] == "마무리" and slide["body"] == "감사합니다"
    slide = {"layout": "closing", "title": "핵심 요약 및 승인 요청 사항"}
    deck.fill_closing(slide, {"bullets": ["3분기 매출 128억 원", "1.5억 예산 승인"]})
    assert slide["title"] == "핵심 요약 및 승인 요청 사항" and len(slide["bullets"]) == 2
