"""As reverify-za found them: 「6장 단계를 두 개로 나눠줘」 on a slide whose steps are only a drawn
flow is that slide's edit (never the deck made anew) and is said and changes nothing; a deck cut
to fewer slides counts a table cell under its row's unit as kept and names a chart's value it left
out by its category."""

from __future__ import annotations

import time

import pytest

from app.pipeline import revise_stages
from app.pipeline.stage import Context
from app.services import deck, revise

#: reverify-za kd4 6장: the steps drawn as a flow, no item list
FLOW = {"id": "s6", "body": "", "title": "파드 생성 요청 처리 과정", "layout": "bullets",
        "bullets": [],
        "diagram": {"key": "25fc88b4c798d8fe", "figure": "flow",
                    "source": "flowchart LR\n  s0(\"요청\")\n  s1(\"검증\")\n  s2(\"스케줄링\")\n"
                              "  s3(\"실행\"):::hot\n  s0 --> s1 --> s2 --> s3",
                    "caption": "파드 생성 흐름"}}


@pytest.mark.parametrize("words", ["6장 단계를 두 개로 나눠줘", "6장 단계를 하나로 합쳐줘"])
def test_splitting_one_slides_steps_is_that_slides_edit(words):
    assert not revise.asks_restructure(words)
    assert revise.numbered_parts(words, ["x"] * 8) == [5]
    assert revise.joins_items(words)


def test_a_slide_split_into_slides_is_still_a_new_structure():
    assert revise.asks_restructure("6장을 두 장으로 나눠줘")
    assert revise.asks_restructure("5장과 6장 단계를 합쳐줘")
    started = time.monotonic()
    revise.asks_restructure("나눠" + " " * 50000 + "단계")
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
@pytest.mark.parametrize("ask, said", [
    ("6장 단계를 두 개로 나눠줘", "6장은 항목 목록 없이 흐름도만 있어 그대로 두었습니다."),
    ("Split the items on slide 6 into two",
     "Slide 6 shows only a figure, no list of items to join or split, so it was kept as it was."),
])
async def test_steps_drawn_only_as_a_flow_are_said_and_kept(monkeypatch, ask, said):
    async def rewrite_slide(**_kwargs):
        raise AssertionError("a drawn flow is not rewritten")

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    parts = [{"id": f"t{i}", "layout": "bullets", "title": f"{i}장", "bullets": ["가", "나"]}
             for i in range(1, 6)] + [dict(FLOW)]
    bundle = revise_stages.bundle_for(
        request="강의", instruction=ask, model={"id": "m"}, planner=None,
        api_key="k", is_deck=True, title="강의", data={}, parts=parts, material=[],
        plan=revise.Plan(scope="parts", targets=[5], note=ask), format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})
    bundle.plan.order = [5]
    events = []
    with pytest.raises(revise_stages.Stop):
        async for event in revise_stages.Rewrite().run(bundle, Context()):
            events.append(event)
    assert bundle.document.sections[5] == FLOW and not bundle.document.changed
    assert "".join(e.get("text", "") for e in events if e["type"] == "delta") == said


#: reverify-za kd2 before and after 「슬라이드를 6장으로 줄여줘」 (the slides that matter)
KD2_OLD = [
    {"id": "a", "title": "지역별 매출 현황", "layout": "chart-donut",
     "chart": {"kind": "donut", "unit": "억원", "categories": ["수도권", "영남", "호남", "기타"],
               "series": [{"name": "매출", "values": [71.0, 33.0, 14.0, 10.0]}]}},
    {"id": "b", "title": "영업 성과 및 물류 이슈", "layout": "two-column",
     "bullets": ["신규 거래처 3곳 추가 계약", "영남 물류센터 이전 진행",
                 "출고 지연 평균 4일 발생"]},
]
KD2_NEW = [
    {"id": "c", "title": "3분기 핵심 성과", "layout": "bullets",
     "bullets": ["신규 거래처 3곳 추가 계약도 매출 확대에 기여했습니다"]},
    {"id": "d", "title": "지역별 매출 및 영업 이슈", "layout": "table",
     "rows": [["기준", "수도권", "영남", "호남"], ["매출(억)", "71", "33", "14"],
              ["출고 지연(일)", "-", "4", "-"]]},
]


def test_a_cut_deck_keeps_a_cell_under_its_rows_unit_and_names_a_charts_value_left_out():
    plotted = deck.charted_only(KD2_OLD, KD2_NEW)
    lost = [f for f in deck.lost_across(KD2_OLD, KD2_NEW, plotted) if f not in plotted]
    assert lost == ["기타 10억"]
    assert deck.lost_blocks(KD2_OLD, KD2_NEW) == ["「지역별 매출 현황」 도넛 차트"]
    # a chart none of whose values the new deck shows is said as the chart alone
    bare = [{"id": "e", "title": "요약", "layout": "bullets", "bullets": ["출고 지연 4일"]}]
    plotted = deck.charted_only(KD2_OLD, bare)
    assert [f for f in deck.lost_across(KD2_OLD, bare, plotted) if f not in plotted] == [
        "신규 거래처 3곳"]
    started = time.monotonic()
    deck.lost_across([{"rows": [["a", "b"], ["x(" + " " * 50000, "4"]]}], [])
    assert time.monotonic() - started < 0.5
