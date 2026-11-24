"""A new workbook from what the agent's `xlsx_write` is given: sheets of rows (a formula is a text
starting with `=`), widths, number formats and a header. What the file holds is exactly what was
given; the values the formulas give are calculated after (`sheet_calc`)."""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import unicodedata
from typing import Any

import openpyxl
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.services import sheet_calc, sheet_edit
from app.services.sheet_edit import SheetError

MAX_SHEETS = 20
MAX_CELLS = 50_000
HEADER_FILL = "#E8EEF6"
_BAD_NAME = re.compile(r"[\[\]:*?/\\]")


def _wide(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _fill(color: str) -> PatternFill:
    return PatternFill("solid", fgColor=color.lstrip("#"), bgColor="000000")


def _apply(cell: Any, style: dict[str, Any], border: dict[str, Any]) -> None:
    if "numFmt" in style:
        cell.number_format = style["numFmt"]
    if font := style.get("font"):
        old = cell.font
        cell.font = Font(
            name=font.get("name", old.name), sz=font.get("sz", old.sz),
            b=font.get("b", old.b), i=font.get("i", old.i), u="single" if font.get("u") else None,
            strike=font.get("s", old.strike),
            color=font["color"].lstrip("#") if "color" in font else old.color)
    if "fill" in style:
        cell.fill = _fill(style["fill"]) if style["fill"] else PatternFill(fill_type=None)
    if align := style.get("align"):
        cell.alignment = Alignment(horizontal=align.get("h"), vertical=align.get("v"),
                                   wrap_text=align.get("wrap"))
    if border:
        def side(key: str, current: Any) -> Side:
            if key not in border:
                return current
            want = border[key]
            return Side(style=want["style"], color=want["color"].lstrip("#")) if want else Side()
        old = cell.border
        cell.border = Border(left=side("l", old.left), right=side("r", old.right),
                             top=side("t", old.top), bottom=side("b", old.bottom))


def _name(raw: Any, taken: list[str], number: int) -> str:
    name = " ".join(str(raw or "").split()) or f"시트{number}"
    if len(name) > 31 or _BAD_NAME.search(name) or name[0] == "'" or name[-1] == "'":
        raise SheetError(f"시트 이름으로 쓸 수 없습니다(31자까지, [ ] : * ? / \\ 불가): {name}")
    if name.casefold() in (t.casefold() for t in taken):
        raise SheetError(f"시트 이름이 겹칩니다: {name}")
    return name


_TOTAL = re.compile(r"^\s*(?:합계|총계|계|소계|총합|total|sum)\s*$", re.I)


def _chart(sheet: Any, spec: dict[str, Any], rows: list[list[Any]], cols: int) -> None:
    """A native Excel chart over the table: `x` the category column and `y` the value columns,
    each a header text or a column letter; the header row and a closing 합계 row are not data."""
    if not isinstance(spec, dict):
        raise SheetError("chart는 {type, x, y, title} 객체입니다.")
    kind = str(spec.get("type") or "bar")
    if kind not in ("bar", "line", "stacked"):
        raise SheetError("chart.type은 bar, line, stacked 중 하나입니다.")
    heads = [str(v) for v in (rows[0] if rows else [])]

    def column(ref: Any) -> int:
        text = str(ref or "").strip()
        if text in heads:
            return heads.index(text) + 1
        try:
            return sheet_edit.col_of(text) + 1
        except SheetError:
            raise SheetError(f"chart의 열 「{text}」을 첫 행의 머리글이나 열 문자(A, B)로 "
                             "적으세요.") from None

    x = column(spec.get("x"))
    ys = [column(y) for y in (spec.get("y") or [])]
    if not ys or max([x, *ys]) > max(cols, 1):
        raise SheetError("chart.y에 값 열이 하나 이상 있어야 합니다.")
    last = len(rows)
    if last > 2 and rows[-1] and _TOTAL.match(str(rows[-1][0] if rows[-1] else "")):
        last -= 1
    if last < 2:
        raise SheetError("차트에는 머리글 아래 데이터 행이 하나 이상 있어야 합니다.")
    chart: Any = LineChart() if kind == "line" else BarChart()
    if kind == "stacked":
        chart.grouping, chart.overlap = "stacked", 100
    chart.title = str(spec.get("title") or "")[:120] or None
    chart.width, chart.height = 18, 9
    for y in ys:
        chart.add_data(Reference(sheet, min_col=y, min_row=1, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=x, min_row=2, max_row=last))
    sheet.add_chart(chart, str(spec.get("anchor") or f"{get_column_letter(cols + 2)}2"))


def _widths(spec: Any, count: int) -> dict[int, int]:
    """Column index (0-based) -> px, from `{"A": 120, "C:D": 90}` or a list in column order."""
    out: dict[int, int] = {}
    if isinstance(spec, list):
        spec = {get_column_letter(i + 1): px for i, px in enumerate(spec)}
    if not isinstance(spec, dict):
        return out
    for key, px in spec.items():
        first, _, last = str(key).partition(":")
        try:
            width = int(px)
        except (TypeError, ValueError):
            raise SheetError(f"widths의 값은 픽셀 수입니다: {px!r}") from None
        if not 20 <= width <= 800:
            raise SheetError("열 너비는 20~800px 사이입니다.")
        a, b = sheet_edit.col_of(first), sheet_edit.col_of(last or first)
        for c in range(min(a, b), max(a, b) + 1):
            out[c] = width
    return out


def build(args: dict[str, Any]) -> tuple[str, bytes]:
    title = " ".join(str(args.get("title") or "").split())[:120]
    sheets = args.get("sheets")
    if not title:
        raise SheetError("title이 있어야 합니다.")
    if not isinstance(sheets, list) or not sheets:
        raise SheetError("sheets에 시트가 하나 이상 있어야 합니다.")
    if len(sheets) > MAX_SHEETS:
        raise SheetError(f"시트는 {MAX_SHEETS}개까지입니다.")
    book = openpyxl.Workbook()
    book.remove(book.active)
    taken: list[str] = []
    total = 0
    for number, spec in enumerate(sheets, 1):
        if not isinstance(spec, dict):
            raise SheetError("sheets의 각 항목은 {name, rows, …} 객체입니다.")
        name = _name(spec.get("name"), taken, number)
        taken.append(name)
        sheet = book.create_sheet(name)
        rows = spec.get("rows")
        if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
            raise SheetError(f"「{name}」의 rows는 행의 목록입니다: [[칸, 칸], [칸, 칸]]")
        total += sum(len(r) for r in rows)
        if total > MAX_CELLS:
            raise SheetError(f"한 통합 문서에 {MAX_CELLS}칸까지 쓸 수 있습니다. 나눠서 "
                             "xlsx_edit set_cells로 더하세요.")
        width_of: dict[int, int] = {}
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                op = sheet_edit._cell_ops(name, r, c, value)  # noqa: SLF001
                if op.get("f"):
                    sheet.cell(r + 1, c + 1).value = "=" + op["f"]
                elif op.get("v") is not None:
                    cell = sheet.cell(r + 1, c + 1)
                    cell.value = op["v"]
                    if fmt := (op.get("s") or {}).get("numFmt"):
                        cell.number_format = fmt
                    text = value if isinstance(value, str) else str(op["v"])
                    width_of[c] = max(width_of.get(c, 0), _wide(text))
        cols = max((len(r) for r in rows), default=0)
        header = spec.get("header", True) is not False and bool(rows)
        if header:
            for c in range(len(rows[0])):
                cell = sheet.cell(1, c + 1)
                _apply(cell, {"font": {"b": True}, "fill": HEADER_FILL,
                              "align": {"h": "center", "v": "center"}},
                       {"b": {"style": "thin", "color": "#7F8FA6"}})
            sheet.freeze_panes = "A2"
        for entry in spec.get("styles") or []:
            if not isinstance(entry, dict):
                raise SheetError("styles의 각 항목은 {range, numFmt, bold, fill, …} 객체입니다.")
            style = sheet_edit._style_of(entry)  # noqa: SLF001
            box = sheet_edit.range_of(entry.get("range"), len(rows), cols)
            if sheet_edit._count(box) > sheet_edit.MAX_CELLS:  # noqa: SLF001
                raise SheetError("styles의 범위가 너무 큽니다.")
            for r in range(box[0], box[2] + 1):
                for c in range(box[1], box[3] + 1):
                    _apply(sheet.cell(r + 1, c + 1), style,
                           sheet_edit._borders(entry, box, r, c))  # noqa: SLF001
        if spec.get("chart"):
            _chart(sheet, spec["chart"], rows, cols)
        for ref in spec.get("merges") or []:
            box = sheet_edit.range_of(ref, len(rows), cols)
            sheet.merge_cells(sheet_edit.span(box))
        given = _widths(spec.get("widths"), cols)
        for c in range(cols):
            px = given.get(c) or min(max(width_of.get(c, 6) * 7 + 18, 64), 360)
            sheet.column_dimensions[get_column_letter(c + 1)].width = round((px - 5) / 7, 2)
        if freeze := spec.get("freeze"):
            sheet.freeze_panes = str(freeze)
    out = io.BytesIO()
    book.save(out)
    return title, sheet_calc.refresh(out.getvalue())


def _plain(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if not (value.hour or value.minute or value.second) \
            else value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat()
    return value


def rows_from_file(name: str, data: bytes, sheet: str = "") -> list[list[Any]]:
    """The cells of an attached CSV or Excel file, as written (a formula stays one): the rows
    `xlsx_write` puts in a sheet without the model retyping them."""
    lower = name.lower()
    rows: list[list[Any]]
    if lower.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        try:
            book = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        except Exception:  # noqa: BLE001
            raise SheetError(f"「{name}」을 엑셀 파일로 읽지 못했습니다.") from None
        try:
            if sheet and sheet not in book.sheetnames:
                raise SheetError(f"「{name}」에 시트 「{sheet}」이 없습니다. 있는 시트: "
                                 + ", ".join(f"「{n}」" for n in book.sheetnames))
            source = book[sheet] if sheet else book.worksheets[0]
            rows = [[_plain(v) for v in row] for row in source.iter_rows(values_only=True)]
        finally:
            book.close()
    elif lower.endswith((".csv", ".tsv", ".txt")):
        for encoding in ("utf-8-sig", "cp949"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise SheetError(f"「{name}」의 글자 코드를 읽지 못했습니다(UTF-8 또는 CP949).")
        first = text.split("\n", 1)[0]
        delimiter = "\t" if first.count("\t") > first.count(",") else ","
        rows = [[(cell if cell != "" else None) for cell in row]
                for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    else:
        raise SheetError(f"「{name}」은 CSV나 엑셀(.xlsx) 파일이 아닙니다.")
    while rows and all(v is None for v in rows[-1]):
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    while width and all(len(r) < width or r[width - 1] is None for r in rows):
        width -= 1
    return [list(r[:width]) for r in rows]
