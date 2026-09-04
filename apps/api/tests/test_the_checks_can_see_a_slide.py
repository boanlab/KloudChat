"""`from_slides` feeds table, figure and chart slides to the checks as sentences."""

from __future__ import annotations

from app.services import lint


def test_a_table_row_reaches_the_checks_as_one_claim() -> None:
    parts = lint.from_slides(
        [
            {
                "title": "대안 비교",
                "layout": "table",
                "rows": [["기준", "대안 A"], ["초기 비용", "약 3억 원"]],
            }
        ]
    )
    # One line per row, not per cell: a claim in a table is the row.
    assert "초기 비용 약 3억 원" in parts[0].lines


def test_a_figure_reaches_the_checks_with_what_it_counts() -> None:
    parts = lint.from_slides(
        [{"title": "성과", "layout": "metrics", "metrics": [["32%", "오탐 감소"]]}]
    )
    assert "32% 오탐 감소" in parts[0].lines


def test_a_chart_reaches_the_checks_as_sentences() -> None:
    """A chart reaches the checks as label-unit-value sentences."""
    parts = lint.from_slides(
        [
            {
                "title": "추이",
                "layout": "chart",
                "chart": {
                    "kind": "bar",
                    "unit": "건",
                    "categories": ["1분기", "2분기"],
                    "series": [{"name": "처리 건수", "values": [120, 210]}],
                },
            }
        ]
    )
    assert "1분기 처리 건수 120건" in parts[0].lines
    assert "2분기 처리 건수 210건" in parts[0].lines


def test_a_chart_written_as_pairs_is_read_too() -> None:
    # Series arrive as tuples (`_chart_of`) or dicts (from the database).
    parts = lint.from_slides(
        [
            {
                "title": "추이",
                "chart": {"categories": ["1월"], "series": [("평균", [3.2])], "unit": "초"},
            }
        ]
    )
    assert "1월 평균 3.2초" in parts[0].lines


def test_a_rule_fires_on_a_cell_it_could_not_see_before() -> None:
    """A rule fires on an unfilled table cell."""
    unfilled = [
        {"title": "대안 비교", "layout": "table", "rows": [["기준", "값"], ["비용", "TBD"]]}
    ]
    findings = lint.check(lint.from_slides(unfilled), slides=True)
    assert [f for f in findings if f.rule == "placeholder"], findings

    # A clean slide stays clean.
    filled = [
        {"title": "대안 비교", "layout": "table", "rows": [["기준", "값"], ["비용", "3억"]]}
    ]
    assert not [
        f for f in lint.check(lint.from_slides(filled), slides=True) if f.rule == "placeholder"
    ]


def test_a_slide_with_none_of_them_is_unchanged() -> None:
    parts = lint.from_slides([{"title": "요약", "bullets": ["가", "나"], "body": "다"}])
    assert parts[0].lines == ["가", "나", "다"]
