"""An action-item row keeps the deadline the material gives its owner for that task — written
by the writer, or filled in when the writer left 「[기한]」 — and a day the material gives only
for something else (a 12/31 in another file) becomes 「[기한]」."""

from app.services.report import _unsourced_owner_dates

SOURCE = """## 주간회의록_20261110.docx
일시: 2026-11-10(화) 10:00
- 4분기 매출 전망 보고서 초안 → 임하준, 11/17까지
- 거래처 계약 갱신 협의 → 강민재, 11/20까지
- 연말 프로모션 기획안 → 최유나, 11/28까지

## 사업계획.docx
사업 기간: 2026-01-01 ~ 2026-12-31
"""

TABLE = """| 할 일 | 담당 | 기한 |
|---|---|---|
| 4분기 매출 전망 보고서 초안 | 임하준 | [기한] |
| 거래처 계약 갱신 협의 | 강민재 | 2026-12-31(목) |
| 연말 프로모션 기획안 | 최유나 | 11/28 |
"""


def test_deadlines_follow_the_owner_s_own_source_line():
    rows = _unsourced_owner_dates(TABLE, SOURCE).splitlines()
    # left blank by the writer: the source's day for that owner and task
    assert rows[2] == "| 4분기 매출 전망 보고서 초안 | 임하준 | 11/17 |"
    # a day the source gives for something else: blanked, then the row's own day put in
    assert rows[3] == "| 거래처 계약 갱신 협의 | 강민재 | 11/20 |"
    assert rows[4] == "| 연말 프로모션 기획안 | 최유나 | 11/28 |"


def test_no_day_is_filled_where_the_source_gives_none_or_several():
    table = ("| 할 일 | 담당 | 기한 |\n|---|---|---|\n"
             "| 고객 설문 정리 | 박서준 | [기한] |\n| 보고서 초안 | 임하준 | [기한] |\n")
    source = "- 고객 설문 정리 → 박서준\n- 보고서 초안 → 임하준, 11/17까지, 수정본은 11/24까지\n"
    assert _unsourced_owner_dates(table, source) == table
