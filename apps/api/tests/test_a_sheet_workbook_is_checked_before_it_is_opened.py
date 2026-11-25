"""A workbook is measured before any sheet service unpacks it, ops the editor could not have
sent are refused, and a file's own mistakes (a wrong <dimension>, a macro, a `=` that is not a
formula) do not cut, run or fail the sheet."""

from __future__ import annotations

import io
import zipfile

import openpyxl
import pytest

from app.services import sheet_build, sheet_doc, sheet_edit, sheet_guard, sheet_model, sheet_patch


def _book(rows: int = 3, cols: int = 2) -> bytes:
    book = openpyxl.Workbook()
    ws = book.active
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            ws.cell(r, c, f"{r}-{c}")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _with(data: bytes, extra: dict[str, bytes] | None = None, edit=None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if edit:
                body = edit(info.filename, body)
            dst.writestr(info.filename, body)
        for name, body in (extra or {}).items():
            dst.writestr(name, body)
    return out.getvalue()


def _bomb() -> bytes:
    """A workbook whose sheet part inflates ~40 MB from a few KB."""
    return _with(_book(), edit=lambda name, body: (
        b"<worksheet>" + b"0" * (40 * 1024 * 1024) + b"</worksheet>"
        if name == "xl/worksheets/sheet1.xml" else body))


def test_an_inflated_workbook_is_refused_everywhere_a_sheet_service_opens_one():
    data = _bomb()
    assert len(data) < 2 * 1024 * 1024
    assert sheet_doc.file_format(data) == ""
    with pytest.raises(sheet_guard.WorkbookRefused):
        sheet_model.to_model(data)
    with pytest.raises(sheet_patch.PatchError):
        sheet_patch.apply(data, [{"sheet": "Sheet", "r": 0, "c": 0, "v": 1}])
    with pytest.raises(sheet_edit.SheetError):
        sheet_edit._dims(data)  # noqa: SLF001
    with pytest.raises(sheet_edit.SheetError):
        sheet_build.rows_from_file("big.xlsx", data)
    assert sheet_doc.describe(data, "xlsx")["cells"] == 0


def test_a_workbook_over_the_size_cap_is_refused(monkeypatch):
    monkeypatch.setattr(sheet_guard, "MAX_BYTES", 100)
    with pytest.raises(sheet_guard.WorkbookRefused):
        sheet_guard.check(_book())


def test_a_macro_workbook_renamed_xlsx_is_not_a_sheet():
    data = _with(_book(), {"xl/vbaProject.bin": b"\x00macro"})
    assert sheet_doc.file_format(data) == ""
    assert sheet_doc.file_format(_book()) == "xlsx"


def test_a_wrong_dimension_does_not_cut_the_table_short():
    data = _with(_book(rows=20, cols=6), edit=lambda n, b: (
        __import__("re").sub(rb'<dimension ref="[^"]*"/>', b'<dimension ref="A1:B5"/>', b)
        if n == "xl/worksheets/sheet1.xml" else b))
    rows = sheet_build.rows_from_file("t.xlsx", data)
    assert len(rows) == 20 and len(rows[0]) == 6
    assert sheet_edit._dims(data) == {"Sheet": (20, 6)}  # noqa: SLF001
    assert sheet_doc.describe(data, "xlsx")["cells"] == 120


@pytest.mark.parametrize("op", [
    {"sheet": "Sheet", "r": -1, "c": 0, "v": 1},
    {"sheet": "Sheet", "r": 0, "c": 16384, "v": 1},
    {"sheet": "Sheet", "r": 1_048_576, "c": 0, "v": 1},
    {"sheet": "Sheet", "c": 0, "v": 1},
    {"sheet": "Sheet", "r": "a", "c": 0, "v": 1},
    {"sheet": "Sheet", "op": "width", "c": 0, "px": "wide"},
    {"sheet": "Sheet", "op": "width", "c": -2, "px": 90},
    {"sheet": "Sheet", "op": "merge", "ref": "not a range"},
    {"sheet": "Sheet", "op": "insertRows", "at": -1},
    {"sheet": "Sheet", "op": "explode"},
    "not an object",
])
def test_an_op_the_editor_could_not_send_is_a_patch_error_not_a_server_error(op):
    with pytest.raises(sheet_patch.PatchError):
        sheet_patch.apply(_book(), [op])


def test_many_cell_edits_land_in_order_with_the_row_index():
    ops = [{"sheet": "Sheet", "r": r, "c": 0, "v": r} for r in (9, 2, 5, 0, 7)]
    out = sheet_patch.apply(_book(rows=1, cols=1), ops)
    ws = openpyxl.load_workbook(io.BytesIO(out)).active
    assert [ws.cell(r + 1, 1).value for r in (0, 2, 5, 7, 9)] == [0, 2, 5, 7, 9]
    numbers = [row[0].row for row in ws.iter_rows()]
    assert numbers == sorted(numbers)


def test_the_editors_formulas_pass_the_same_check_as_the_agents():
    sheet_edit.ui_formula_ok([{"f": "SUM(A1:A3)"}, {"v": "=== 구분 ==="}, {"f": '="a|b"'}])
    for bad in ('WEBSERVICE("http://x")', "[Other.xlsx]Sheet1!A1", "cmd|' /c calc'!A1"):
        with pytest.raises(sheet_patch.PatchError):
            sheet_edit.ui_formula_ok([{"f": bad}])


def test_a_text_from_a_file_that_only_starts_with_equals_stays_text():
    value = sheet_edit.as_file_value("=== 구분 ===")
    assert isinstance(value, sheet_edit.LiteralText)
    op = sheet_edit._cell_ops("Sheet", 0, 0, value)  # noqa: SLF001
    assert op == {"sheet": "Sheet", "r": 0, "c": 0, "v": "=== 구분 ==="}
    assert sheet_edit.as_file_value("=SUM(A1:A2)") == "=SUM(A1:A2)"
    rows = sheet_build.rows_from_file("t.csv", "a,b\n=== 구분 ===,2\n".encode())
    assert isinstance(rows[1][0], sheet_edit.LiteralText)
