"""The browser grid edits cells by patching the sheet XML of the original `.xlsx`, so a
chart, a text box, a sparkline, a picture and the other sheets come back exactly as they went in.
`sheet_model` reads the workbook for the grid and names what the grid cannot draw."""

from __future__ import annotations

import io
import zipfile

import openpyxl
from openpyxl.chart import BarChart, Reference
from openpyxl.drawing.image import Image
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Font, PatternFill
from PIL import Image as PILImage

from app.services import sheet_model, sheet_patch

SHEET = "매출현황"
TEXT_BOX = (
    '<xdr:twoCellAnchor xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/'
    'spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<xdr:from><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>9'
    '</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>7</xdr:col><xdr:colOff>0'
    '</xdr:colOff><xdr:row>13</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to><xdr:sp macro="" '
    'textlink=""><xdr:nvSpPr><xdr:cNvPr id="99" name="memo"/><xdr:cNvSpPr txBox="1"/>'
    '</xdr:nvSpPr><xdr:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
    '<xdr:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>검토 메모</a:t></a:r></a:p></xdr:txBody>'
    '</xdr:sp><xdr:clientData/></xdr:twoCellAnchor>'
)
SPARK = (
    '<extLst><ext uri="{05C60535-1F16-4fd2-B633-F4F36F0B64E0}" xmlns:x14="http://schemas.'
    'microsoft.com/office/spreadsheetml/2009/9/main"><x14:sparklineGroups xmlns:xm="http://'
    'schemas.microsoft.com/office/excel/2006/main"><x14:sparklineGroup><x14:sparklines>'
    '<x14:sparkline><xm:f>매출현황!B3:D3</xm:f><xm:sqref>L3</xm:sqref></x14:sparkline>'
    '</x14:sparklines></x14:sparklineGroup></x14:sparklineGroups></ext></extLst>'
)


def _workbook() -> bytes:
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = SHEET
    ws.append(["지점", "1월", "2월", "합계"])
    for i, row in enumerate((("서울", 10, 20), ("부산", 5, 6)), start=2):
        ws.append([*row, f"=SUM(B{i}:C{i})"])
    ws["A1"].font = Font(bold=True)
    ws["A1"].fill = PatternFill("solid", fgColor="DDEBF7")
    ws["B2"].number_format = "#,##0"
    ws.merge_cells("F1:G1")
    ws.column_dimensions["A"].width = 20
    ws.freeze_panes = "B2"
    ws.conditional_formatting.add("B2:B3", CellIsRule(operator="greaterThan", formula=["7"]))
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=4, min_row=1, max_row=3), titles_from_data=True)
    ws.add_chart(chart, "A10")
    png = io.BytesIO()
    PILImage.new("RGB", (8, 8), (200, 30, 30)).save(png, "PNG")
    png.seek(0)
    ws.add_image(Image(png), "H10")
    book.create_sheet("단가표")["A1"] = "후면"
    out = io.BytesIO()
    book.save(out)
    # a text box and a sparkline, which openpyxl would drop on its own save
    src = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    merged = io.BytesIO()
    with zipfile.ZipFile(merged, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            body = src.read(name)
            if name == "xl/drawings/drawing1.xml":
                body = body.decode().replace("</wsDr>", TEXT_BOX + "</wsDr>").encode()
            if name == "xl/worksheets/sheet1.xml":
                body = body.decode().replace("</worksheet>", SPARK + "</worksheet>").encode()
            dst.writestr(name, body)
    return merged.getvalue()


def _parts(data: bytes) -> dict[str, bytes]:
    z = zipfile.ZipFile(io.BytesIO(data))
    return {n: z.read(n) for n in z.namelist()}


def test_the_grid_model_carries_cells_styles_and_names_what_it_cannot_draw():
    model = sheet_model.to_model(_workbook())
    sheet = model["sheets"][0]
    by = {(c["r"], c["c"]): c for c in sheet["cells"]}
    assert [s["name"] for s in model["sheets"]] == [SHEET, "단가표"]
    assert by[(1, 3)]["f"] == "SUM(B2:C2)"
    assert by[(0, 0)]["s"]["font"]["b"] is True and by[(0, 0)]["s"]["fill"] == "#DDEBF7"
    assert by[(1, 1)]["s"]["numFmt"] == "#,##0"
    assert sheet["merges"] == ["F1:G1"]
    assert sheet["colWidths"]["0"] > 100
    assert sheet["freeze"] == {"r": 1, "c": 1}
    assert {"chart:1", "picture:1", "shape", "sparkline"} <= set(model["kept"])


def test_a_hand_edit_changes_its_cells_and_leaves_every_other_part_byte_for_byte():
    before = _workbook()
    after = sheet_patch.apply(before, [
        {"sheet": SHEET, "r": 1, "c": 1, "v": 11},
        {"sheet": SHEET, "r": 1, "c": 3, "f": "SUM(B2:C2)", "v": 31},
        {"sheet": SHEET, "r": 5, "c": 0, "v": "새 줄 한글"},
        {"sheet": SHEET, "r": 0, "c": 0, "s": {"fill": "#FFFF00", "numFmt": "0.0%"}},
        {"sheet": SHEET, "op": "width", "c": 1, "px": 140},
        {"sheet": SHEET, "op": "merge", "ref": "A8:B8"},
        {"sheet": "단가표", "r": 0, "c": 0, "v": "바뀐"},
    ])
    old, new = _parts(before), _parts(after)
    touched = {"xl/workbook.xml", "xl/styles.xml", "xl/worksheets/sheet1.xml",
               "xl/worksheets/sheet2.xml"}
    assert {n for n in old if old[n] != new.get(n)} == touched
    assert b"sparklineGroup" in new["xl/worksheets/sheet1.xml"]
    assert "검토 메모".encode() in new["xl/drawings/drawing1.xml"]
    book = openpyxl.load_workbook(io.BytesIO(after))
    ws = book[SHEET]
    assert (ws["B2"].value, ws["D2"].value) == (11, "=SUM(B2:C2)")
    assert ws["A6"].value == "새 줄 한글"
    assert ws["A1"].fill.fgColor.rgb.endswith("FFFF00") and ws["A1"].font.b
    assert ws["A1"].number_format == "0.0%"
    assert ws.column_dimensions["B"].width == 19.29
    assert {str(r) for r in ws.merged_cells.ranges} == {"F1:G1", "A8:B8"}
    assert book["단가표"]["A1"].value == "바뀐"
    assert len(ws._charts) == 1 and len(ws._images) == 1
    values = openpyxl.load_workbook(io.BytesIO(after), data_only=True)[SHEET]
    assert values["D2"].value == 31


def test_a_cleared_cell_keeps_its_style_and_a_calculation_chain_is_dropped():
    book = openpyxl.Workbook()
    book.active["A1"] = 5
    book.active["A1"].fill = PatternFill("solid", fgColor="FF0000")
    out = io.BytesIO()
    book.save(out)
    src = _parts(out.getvalue())
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as z:
        for name, body in src.items():
            z.writestr(name, body)
        z.writestr("xl/calcChain.xml", b"<calcChain/>")
    cleared = sheet_patch.apply(packed.getvalue(), [{"sheet": "Sheet", "r": 0, "c": 0, "v": None}])
    assert "xl/calcChain.xml" not in _parts(cleared)
    ws = openpyxl.load_workbook(io.BytesIO(cleared)).active
    assert ws["A1"].value is None and ws["A1"].fill.fgColor.rgb.endswith("FF0000")
