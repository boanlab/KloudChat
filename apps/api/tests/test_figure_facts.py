"""A comparison figure is checked against its section's table before it is kept:
a box that says what the table does not leaves the figure out, and a
highlight on an option the report does not recommend comes off."""

from __future__ import annotations

import asyncio

import pytest

from app.services import diagram, diagram_render, diagrams, figure_facts, report

TABLE = """표 1. 대안별 비교

| 구분 | 안1(엑셀 유지) | 안2(그룹웨어) | 안3(무료 캘린더) |
|---|---|---|---|
| 비용(연) | 0원 | 120만 원 | 0원 |
| 중복 방지 | 중복 예약 월 6건 발생 | 중복 방지 | 자료 없음 |
| 보안 | 자료 없음 | 자료 없음 | 개인정보 검토 필요 |
| 도입 기간 | 자료 없음 | 2주 | 1주 |
"""

WRONG = """flowchart LR
  subgraph a["안1 엑셀 유지"]
    direction TB
    a1[중복 방지: 충족]
  end
  subgraph b["안2 그룹웨어"]
    direction TB
    b1[비용: 자료 없음]
    b2[도입 기간: 자료 없음]
  end
  subgraph c["안3 무료 캘린더"]
    direction TB
    c1[보안: 충족]:::hot
  end
"""

RIGHT = """flowchart LR
  subgraph a["안1"]
    a1[비용: 0원]
    a2[중복 방지: 월 6건 발생]
  end
  subgraph c["안3"]
    c1[보안: 개인정보 검토 필요]:::hot
  end
"""


def test_a_box_the_table_contradicts_is_found():
    found = figure_facts.check(WRONG, context=TABLE).problems
    assert len(found) == 4
    assert found[0] == "안1 중복 방지: 그림 「충족」, 표 「중복 예약 월 6건 발생」"
    assert any("안2 비용" in p and "120만 원" in p for p in found)
    assert any("안3 보안" in p for p in found)


def test_a_figure_that_agrees_keeps_its_boxes_but_not_a_highlight_on_another_option():
    kept = figure_facts.check(RIGHT, context=TABLE, document="결론\n\n안2를 권고합니다.")
    assert kept.problems == [] and ":::hot" not in kept.source
    # On the recommended option, or with no recommendation, the highlight stays.
    assert ":::hot" in figure_facts.check(RIGHT, context=TABLE, document="안3을 권고합니다").source
    assert ":::hot" in figure_facts.check(RIGHT, context=TABLE).source


def test_options_are_read_across_the_top_or_down_the_side():
    side = "| 대안 | 비용 | 기간 |\n|---|---|---|\n| 1안 | 0원 | 2주 |\n| 2안 | 120만 원 | 1주 |"
    cells = figure_facts.table_cells(side)
    assert cells[("안2", "비용")] == "120만 원" and cells[("안1", "기간")] == "2주"


def test_make_leaves_a_contradicted_figure_out(monkeypatch):
    async def drawn(**_kw):
        return WRONG, "3안 비교 구조", {"inputTokens": 1, "outputTokens": 1}, b"png"

    monkeypatch.setattr(diagram, "draw_native", drawn)
    planned = diagrams.Planned(2, "compare", "세 안의 비교", "3안 비교 구조", context=TABLE)
    with pytest.raises(figure_facts.Contradicted):
        asyncio.run(diagrams.make(planned, model="m", api_key="k", slide=False))


def test_make_redraws_a_figure_whose_highlight_came_off(monkeypatch):
    async def drawn(**_kw):
        return RIGHT, "비교", {}, b"old"

    async def render(source, accent):
        assert ":::hot" not in source
        return b"new"

    monkeypatch.setattr(diagram, "draw_native", drawn)
    monkeypatch.setattr(diagram_render, "render_png", render)
    planned = diagrams.Planned(2, "compare", "세 안의 비교", "비교", context=TABLE,
                               document="안2를 권고합니다.")
    made, _, png = asyncio.run(diagrams.make(planned, model="m", api_key="k", slide=False))
    assert png == b"new" and ":::hot" not in made["source"]


def test_kpi_rows_between_stray_backticks_become_a_kpi_block():
    body = ("안2는 값을 갖습니다.\n\n`\n\n120만 원 | 안2 연간 비용\n\n6건 | 안1 월별 중복 예약\n\n"
            "2주 | 안2 도입 기간\n\n`\n\n그림 2.")
    assert report.mend_kpi_fences(body) == (
        "안2는 값을 갖습니다.\n\n```kpi\n120만 원 | 안2 연간 비용\n6건 | 안1 월별 중복 예약\n"
        "2주 | 안2 도입 기간\n```\n\n그림 2.")
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert report.mend_kpi_fences(table) == table
    assert report.mend_kpi_fences("A | B 비교는 이렇다") == "A | B 비교는 이렇다"
