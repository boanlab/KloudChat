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
