"""Rows and columns inserted or deleted, sheets added, renamed or deleted: the cells, merges and
widths follow, a rename reaches the formulas and chart ranges that name the sheet, a deleted
sheet takes its own drawing with it, and every other part is copied as it was."""

from __future__ import annotations

import io
import zipfile

import openpyxl
import pytest
from lxml import etree
from openpyxl.chart import BarChart, Reference
from test_a_sheet_edit_keeps_what_the_grid_does_not_draw import SHEET, _parts, _workbook

from app.services import sheet_patch

NS = {"m": sheet_patch.NS}


def _sheet_xml(data: bytes, part: str = "xl/worksheets/sheet1.xml"):
    return etree.fromstring(_parts(data)[part])


def test_inserted_rows_push_cells_and_merges_down_and_leave_other_parts_alone():
    before = _workbook()
    after = sheet_patch.apply(before, [{"sheet": SHEET, "op": "insertRows", "at": 1, "count": 2}])
    ws = openpyxl.load_workbook(io.BytesIO(after))[SHEET]
    assert ws["A1"].value == "지점" and ws["A2"].value is None and ws["A3"].value is None
    assert ws["A4"].value == "서울" and ws["A5"].value == "부산"
    assert {str(r) for r in ws.merged_cells.ranges} == {"F1:G1"}
    old, new = _parts(before), _parts(after)
    assert new["xl/drawings/drawing1.xml"] == old["xl/drawings/drawing1.xml"]
    assert b"sparklineGroup" in new["xl/worksheets/sheet1.xml"]
    assert new["xl/worksheets/sheet2.xml"] == old["xl/worksheets/sheet2.xml"]
    # the conditional format range moved with its cells
    cf = _sheet_xml(after).find("m:conditionalFormatting", NS)
    assert cf.get("sqref") == "B4:B5"


def test_deleted_rows_and_columns_take_their_cells_and_close_up():
    after = sheet_patch.apply(_workbook(), [
        {"sheet": SHEET, "op": "deleteRows", "at": 1, "count": 1},
        {"sheet": SHEET, "op": "deleteCols", "at": 0, "count": 1},
    ])
    ws = openpyxl.load_workbook(io.BytesIO(after))[SHEET]
    # row 2 (서울) is gone, 부산 is now row 2; column A (지점) is gone, 1월 is now column A
    assert ws["A1"].value == "1월" and ws["A2"].value == 5
    assert {str(r) for r in ws.merged_cells.ranges} == {"E1:F1"}
    # the 20-wide first column was deleted with its width
    assert ws.column_dimensions["A"].width != 20


def test_a_merge_cut_down_to_one_cell_is_dropped_and_one_with_room_shrinks():
    book = openpyxl.Workbook()
    ws = book.active
    ws.merge_cells("B2:C2")
    ws.merge_cells("E2:H3")
    out = io.BytesIO()
    book.save(out)
    after = sheet_patch.apply(out.getvalue(), [
        {"sheet": "Sheet", "op": "deleteCols", "at": 2, "count": 1},  # column C
        {"sheet": "Sheet", "op": "deleteCols", "at": 5, "count": 1},  # an old column G
    ])
    merged = openpyxl.load_workbook(io.BytesIO(after)).active.merged_cells.ranges
    assert {str(r) for r in merged} == {"D2:F3"}


def test_a_shared_formula_keeps_its_meaning_when_its_cells_move():
    book = openpyxl.Workbook()
    ws = book.active
    for i in range(1, 5):
        ws[f"A{i}"] = i
    out = io.BytesIO()
    book.save(out)
    parts = _parts(out.getvalue())
    sheet = parts["xl/worksheets/sheet1.xml"].decode()
    shared = (
        '<row r="1"><c r="B1"><f t="shared" ref="B1:B4" si="0">A1*2</f><v>2</v></c></row>'
        '<row r="2"><c r="B2"><f t="shared" si="0"/><v>4</v></c></row>'
        '<row r="3"><c r="B3"><f t="shared" si="0"/><v>6</v></c></row>'
    )
    sheet = sheet.replace("<sheetData>", "<sheetData>" + shared, 1)
    # the three formula rows replace the plain ones of the same numbers
    root = etree.fromstring(sheet.encode())
    data = root.find("m:sheetData", NS)
    seen = {}
    for row in list(data):
        if row.get("r") in seen:
            data.remove(row)
        seen[row.get("r")] = row
    parts["xl/worksheets/sheet1.xml"] = etree.tostring(root)
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as z:
        for name, body in parts.items():
            z.writestr(name, body)
    after = sheet_patch.apply(packed.getvalue(), [
        {"sheet": "Sheet", "op": "insertRows", "at": 1, "count": 1}])
    formulas = {c.get("r"): c.find("m:f", NS).text for c in
                _sheet_xml(after).iter(f"{{{sheet_patch.NS}}}c") if c.find("m:f", NS) is not None}
    assert formulas == {"B1": "A1*2", "B3": "A2*2", "B4": "A3*2"}


def _two_sheet_book() -> bytes:
    book = openpyxl.Workbook()
    first = book.active
    first.title = "원본"
    first.append(["월", "값"])
    first.append(["1월", 10])
    second = book.create_sheet("요약")
    second["A1"] = "='원본'!B2*2"
    second["A2"] = "=원본!B2+1"
    second["A3"] = '="원본!B2"'
    chart = BarChart()
    chart.add_data(Reference(first, min_col=2, min_row=1, max_row=2), titles_from_data=True)
    second.add_chart(chart, "C3")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_renaming_a_sheet_reaches_formulas_and_chart_ranges_that_name_it():
    after = sheet_patch.apply(_two_sheet_book(), [
        {"sheet": "원본", "op": "renameSheet", "name": "판매 원본"}])
    book = openpyxl.load_workbook(io.BytesIO(after))
    assert book.sheetnames == ["판매 원본", "요약"]
    summary = book["요약"]
    assert summary["A1"].value == "='판매 원본'!B2*2"
    assert summary["A2"].value == "='판매 원본'!B2+1"
    assert summary["A3"].value == '="원본!B2"'  # a string literal is text, not a reference
    chart = _parts(after)["xl/charts/chart1.xml"].decode()
    assert "'판매 원본'!" in chart and "원본!$B" not in chart.replace("판매 원본", "")


def test_a_bad_or_taken_sheet_name_is_refused():
    for name in ("요약", "a/b", "", "x" * 32):
        with pytest.raises(sheet_patch.PatchError):
            sheet_patch.apply(_two_sheet_book(),
                              [{"sheet": "원본", "op": "renameSheet", "name": name}])
    with pytest.raises(sheet_patch.PatchError):
        sheet_patch.apply(_two_sheet_book(), [{"op": "addSheet", "name": "원본"}])


def test_deleting_a_sheet_takes_its_own_chart_and_keeps_the_other_sheet_whole():
    before = _two_sheet_book()
    assert "xl/charts/chart1.xml" in _parts(before)
    after = sheet_patch.apply(before, [{"sheet": "요약", "op": "deleteSheet"}])
    parts = _parts(after)
    assert not any(n.startswith("xl/charts/") or n.startswith("xl/drawings/") for n in parts)
    assert "xl/worksheets/sheet2.xml" not in parts
    book = openpyxl.load_workbook(io.BytesIO(after))
    assert book.sheetnames == ["원본"] and book["원본"]["B2"].value == 10
    types = parts["[Content_Types].xml"].decode()
    assert "sheet2.xml" not in types and "chart1.xml" not in types
    with pytest.raises(sheet_patch.PatchError):
        sheet_patch.apply(after, [{"sheet": "원본", "op": "deleteSheet"}])


def test_an_added_sheet_opens_empty_takes_cells_and_sits_where_asked():
    after = sheet_patch.apply(_two_sheet_book(), [
        {"op": "addSheet", "name": "새 시트", "index": 1},
        {"sheet": "새 시트", "r": 0, "c": 0, "v": "안녕"},
    ])
    book = openpyxl.load_workbook(io.BytesIO(after))
    assert book.sheetnames == ["원본", "새 시트", "요약"]
    assert book["새 시트"]["A1"].value == "안녕"
    assert len(book["요약"]._charts) == 1


def test_a_border_and_a_font_name_are_written_on_a_copy_of_the_cells_own_style():
    book = openpyxl.Workbook()
    book.active["A1"] = "x"
    book.active["A1"].font = openpyxl.styles.Font(bold=True)
    out = io.BytesIO()
    book.save(out)
    after = sheet_patch.apply(out.getvalue(), [{"sheet": "Sheet", "r": 0, "c": 0, "s": {
        "border": {"t": {"style": "thin", "color": "#FF0000"}, "b": {"style": "double"}},
        "font": {"name": "Malgun Gothic"}}}])
    cell = openpyxl.load_workbook(io.BytesIO(after)).active["A1"]
    assert cell.border.top.style == "thin" and cell.border.top.color.rgb.endswith("FF0000")
    assert cell.border.bottom.style == "double" and cell.border.left.style is None
    assert cell.font.name == "Malgun Gothic" and cell.font.b
