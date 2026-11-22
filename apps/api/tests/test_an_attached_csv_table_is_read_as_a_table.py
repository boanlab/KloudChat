"""An attached CSV reaches the checks as tab-separated rows (empty trailing cells kept,
bare-figure columns read as percentages); its figures are the person's."""

from __future__ import annotations

import datetime as dt

from app.services import check_skills, table_pivots, value_guard

CSV = ("## outbound.csv\n"
       "일자\t요일\t출고 주문(건)\t출고 박스(개)\t오출고(건)\t당일 출고율(%)\t비고\n"
       "2026-12-07\t월\t3100\t5200\t2\t97.5\t\n"
       "2026-12-08\t화\t2950\t4980\t1\t98.9\t\n"
       "2026-12-09\t수\t3300\t5510\t4\t93.6\t분류기 정지\n"
       "2026-12-10\t목\t3050\t5120\t2\t98.1\t\n")


def test_a_tsv_row_keeps_its_empty_last_cells():
    found = table_pivots.tables(CSV)
    assert len(found) == 1 and len(found[0]) == 5
    assert all(len(row) == 7 for row in found[0])


def test_a_percent_columns_bare_figures_are_given_percentages():
    basis = value_guard.percent_basis([CSV])
    text = "당일 출고율은 97.5%, 98.9%, 93.6%, 98.1%였습니다."
    assert value_guard.mark_unbacked_percents(text, basis) == (text, 0)


def test_a_percent_not_in_the_column_is_still_marked():
    basis = value_guard.percent_basis([CSV])
    marked, n = value_guard.mark_unbacked_percents("당일 출고율은 63.4%까지 떨어졌습니다.", basis)
    assert n == 1 and value_guard.MARK in marked


def test_a_column_total_of_the_attached_table_stays_unmarked():
    options = {**check_skills.options_for(dt.date(2026, 12, 11), {}, surface="document"),
               "person": CSV}
    answer = "나흘간 출고 주문은 12,400건, 출고 박스는 20,810개였습니다."
    out = check_skills.ground(("grounding-check",), answer, material=CSV, computed=[],
                              options=options, evidence=[])
    assert out.text == answer
