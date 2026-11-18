"""A chart slide's figures come from the pasted table, read by code (the quarterly revenue deck
of the 2026-11-17 benchmark never got a right chart from any model); a request for a chart
with none drawn gets one; an explicit slide count is held; the planner reads a digest."""

from __future__ import annotations

from app.services import deck, source_digest

MATERIAL = """아래 자료로 경영진 보고용 분기 실적 슬라이드 8장을 만들어 줘.
분기별 제품 매출 차트를 꼭 넣어 줘.

2026년 분기별 매출 실적 (단위: 백만 원, (주)새벽별소프트, 재무팀 기준일 2026-11-05)
분기,StarVault,StarGuard,컨설팅,합계,신규 계약(개사),영업이익
1분기,1820,2140,610,4570,14,412
2분기,1950,2310,580,4840,17,455
3분기,2080,2270,집계 중(검수 미완료),집계 중,21,미확정
4분기,,,,,,
연간 목표 19,000(StarVault 8,000, StarGuard 9,200, 컨설팅 1,800).
"""


def test_the_tables_of_the_material_are_read_with_their_title_and_unit() -> None:
    [table] = deck.material_tables([MATERIAL])
    assert table["header"][:4] == ["분기", "StarVault", "StarGuard", "컨설팅"]
    assert table["unit"] == "백만 원" and len(table["rows"]) == 4


def test_the_chart_plots_the_products_that_make_up_the_total_and_nothing_else() -> None:
    chart = deck.code_chart("분기별 제품 매출 추이", [MATERIAL])
    assert chart is not None
    names = [s["name"] for s in chart["series"]]
    # the product columns, never the total, the contract count or the operating profit
    assert names == ["StarVault", "StarGuard", "컨설팅"]
    assert chart["unit"] == "백만 원" and chart["kind"] == "line"
    # a series ends where its figure is still being counted (컨설팅 3분기), the rest are cut
    # to the same width: the figures are the table's own
    assert chart["categories"] == ["1분기", "2분기"]
    assert chart["series"][0]["values"] == [1820.0, 1950.0]
    assert chart["series"][2]["values"] == [610.0, 580.0]


def test_a_title_naming_a_column_plots_that_column() -> None:
    chart = deck.code_chart("분기별 영업이익", [MATERIAL])
    assert chart is not None and [s["name"] for s in chart["series"]] == ["영업이익"]


def test_a_table_that_does_not_fit_the_title_gives_no_chart() -> None:
    assert deck.code_chart("팀 소개", [MATERIAL]) is None
    assert deck.code_chart("분기별 매출", ["표가 없는 자료입니다."]) is None


def test_a_chart_slide_takes_the_tables_figures_over_the_models() -> None:
    slides = [{"layout": "title", "title": "분기 실적"},
              {"layout": "chart", "title": "분기별 제품 매출",
               "chart": {"kind": "bar", "unit": "월", "categories": ["a", "b"],
                         "series": [{"name": "영업이익", "values": [380, 188]}]}}]
    changed = deck.place_code_charts(slides, MATERIAL, [MATERIAL])
    assert changed == [1]
    assert [s["name"] for s in slides[1]["chart"]["series"]][:2] == ["StarVault", "StarGuard"]
    assert slides[1]["chart"]["unit"] == "백만 원"


def test_a_requested_chart_with_none_drawn_turns_the_matching_slide_into_one() -> None:
    slides = [{"layout": "title", "title": "분기 실적 보고"},
              {"layout": "bullets", "title": "핵심 실적", "bullets": ["a"]},
              {"layout": "bullets", "title": "분기별 제품 매출 추이", "bullets": ["차트"]},
              {"layout": "bullets", "title": "결정 사항", "bullets": ["b"]}]
    assert deck.place_code_charts(slides, MATERIAL, [MATERIAL]) == [2]
    assert slides[2]["layout"] == "chart" and "bullets" not in slides[2]
    # no chart asked: nothing is turned into one
    plain = [{"layout": "bullets", "title": "분기별 제품 매출 추이", "bullets": ["x"]}]
    assert deck.place_code_charts(plain, "슬라이드 3장", [MATERIAL]) == []


def test_an_explicit_count_is_held_by_merging_the_closest_content_slides() -> None:
    plan = [{"title": "표지", "layout": "title"}, {"title": "목차", "layout": "agenda"},
            *({"title": f"내용 {i}", "layout": "bullets"} for i in range(1, 8)),
            {"title": "마무리", "layout": "closing"}]
    held = deck.merge_to_count(plan, 8)
    assert len(held) == 8 and held[0]["layout"] == "title" and held[-1]["layout"] == "closing"


def test_the_planner_reads_a_digest_not_every_sentence() -> None:
    paper = ("[1] arXiv:2309.06180 Efficient Memory Management for LLM Serving. "
             + "Existing systems waste memory through fragmentation. " * 40
             + "vLLM improves throughput by 2-4x over prior systems. "
             + "\nTable 1. Memory waste in existing systems\n" + "filler words here. " * 200)
    digested = source_digest.digest([paper, paper, paper], total=3000)
    assert sum(len(d) for d in digested) <= 3000 + 100
    assert all("arXiv:2309.06180" in d for d in digested)
    assert source_digest.digest(["짧은 자료"]) == ["짧은 자료"]
    whole = source_digest.digest_one(paper)
    assert "2-4x" in whole and "Table 1" in whole and len(whole) < len(paper) / 2
