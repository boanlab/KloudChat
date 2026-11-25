"""SUMIF/COUNTIF take `*` and `?`, COUNTA counts a formula that gives "", and a formula the
calculator has no function for is named instead of quoted from a stale cache."""

from __future__ import annotations

import io

import openpyxl

from app.services import sheet_calc, sheet_edit


def _book(fill) -> bytes:
    book = openpyxl.Workbook()
    fill(book.active)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _teams(ws):
    for r, (name, n) in enumerate([("영업팀", 1), ("개발팀", 2), ("영업", 4), ("팀장", 8)], 1):
        ws.cell(r, 1, name)
        ws.cell(r, 2, n)
    ws["D1"] = '=SUMIF(A1:A4,"*팀",B1:B4)'
    ws["D2"] = '=COUNTIF(A1:A4,"영?팀")'
    ws["D3"] = '=COUNTIF(A1:A4,"<>*팀")'
    ws["D4"] = '=COUNTIF(A1:A4,"영업")'


def test_wildcards_match_whole_cells_as_in_excel():
    got = sheet_calc.values(_book(_teams))
    assert got[("Sheet", 1, 4)] == 3
    assert got[("Sheet", 2, 4)] == 1
    assert got[("Sheet", 3, 4)] == 2
    assert got[("Sheet", 4, 4)] == 1


def test_counta_counts_a_formula_that_gives_an_empty_text():
    def fill(ws):
        ws["A1"] = '=""'
        ws["A3"] = "x"
        ws["B1"] = "=COUNTA(A1:A3)"

    assert sheet_calc.values(_book(fill))[("Sheet", 1, 2)] == 2


def _lookup(ws):
    ws["A1"] = 1
    ws["B1"] = 10
    ws["C1"] = "=VLOOKUP(1,A1:B1,2,FALSE)"
    ws["C2"] = "=SUM(A1:B1)"


def test_a_formula_without_a_function_here_is_named():
    assert sheet_calc.uncomputed(_book(_lookup)) == ["Sheet!C1"]
    assert sheet_calc.uncomputed(_book(_teams)) == []


def test_the_agents_read_warns_about_values_it_could_not_check():
    text = sheet_edit.outline(_book(_lookup))
    assert "계산하지 못한 수식 칸" in text and "Sheet!C1" in text
    assert "계산하지 못한 수식 칸" not in sheet_edit.outline(_book(_teams))
