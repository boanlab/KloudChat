"""An `.xlsx` as an editor-neutral JSON model (sheets, sparse cells, styles, merges,
sizes) for the browser grid, plus a list of what the file holds that the grid cannot show.

Read only (`openpyxl`): the browser sends back changed cells and `sheet_patch` writes them into
the original package, so nothing listed in `kept` is lost on save.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from typing import Any

import openpyxl
from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.worksheet.worksheet import Worksheet

from app.services import sheet_guard

#: Office 2013+ theme colours, by theme index (lt1 dk1 lt2 dk2 accent1..6 hlink folHlink)
_THEME = ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5", "FFC000",
          "5B9BD5", "70AD47", "0563C1", "954F72"]
EPOCH = dt.datetime(1899, 12, 30)
#: a grid this size or smaller is shown whole; a bigger one is cut (`truncated`)
MAX_CELLS = 200_000


def _rgb(color: Any) -> str | None:
    """`#rrggbb` of an openpyxl colour (rgb, theme + tint, indexed), else None."""
    if color is None:
        return None
    kind = color.type
    value: str | None = None
    if kind == "rgb" and isinstance(color.rgb, str):
        value = color.rgb[-6:]
    elif kind == "theme" and isinstance(color.theme, int) and color.theme < len(_THEME):
        value = _THEME[color.theme]
    elif kind == "indexed" and isinstance(color.indexed, int) and color.indexed < len(COLOR_INDEX):
        value = COLOR_INDEX[color.indexed][-6:]
    if not value or not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return None
    if color.tint:
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
        mix = (lambda x: round(x * (1 + color.tint))) if color.tint < 0 else (
            lambda x: round(x + (255 - x) * color.tint))
        value = "".join(f"{max(0, min(255, mix(x))):02X}" for x in (r, g, b))
    return "#" + value.upper()


def _style(cell: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    font = cell.font
    if font:
        f: dict[str, Any] = {}
        if font.b:
            f["b"] = True
        if font.i:
            f["i"] = True
        if font.u:
            f["u"] = True
        if font.strike:
            f["s"] = True
        if font.sz and font.sz != 11:
            f["sz"] = float(font.sz)
        if font.name and font.name != "Calibri":
            f["name"] = font.name
        if (c := _rgb(font.color)) and c != "#000000":
            f["color"] = c
        if f:
            out["font"] = f
    if cell.fill and cell.fill.fill_type == "solid" and (c := _rgb(cell.fill.fgColor)):
        out["fill"] = c
    if cell.number_format and cell.number_format != "General":
        out["numFmt"] = cell.number_format
    al = cell.alignment
    if al and (al.horizontal or al.vertical or al.wrap_text):
        out["align"] = {k: v for k, v in (("h", al.horizontal), ("v", al.vertical),
                                          ("wrap", True if al.wrap_text else None)) if v}
    side = {}
    for name, edge in (("t", cell.border.top), ("b", cell.border.bottom),
                       ("l", cell.border.left), ("r", cell.border.right)):
        if edge and edge.style:
            side[name] = {"style": edge.style, "color": _rgb(edge.color) or "#000000"}
    if side:
        out["border"] = side
    return out


def _serial(value: Any) -> float | None:
    if isinstance(value, dt.datetime):
        delta = value - EPOCH
        return delta.days + delta.seconds / 86400
    if isinstance(value, dt.date):
        return float((dt.datetime(value.year, value.month, value.day) - EPOCH).days)
    if isinstance(value, dt.time):
        return (value.hour * 3600 + value.minute * 60 + value.second) / 86400
    return None


def kept(data: bytes) -> list[str]:
    """What the package holds that the grid does not draw or edit; a save leaves each as it was."""
    names = sheet_guard.open_zip(data).namelist()
    out: list[str] = []
    for label, test in (
        ("chart", lambda n: n.startswith("xl/charts/chart")),
        ("picture", lambda n: n.startswith("xl/media/")),
        ("pivot", lambda n: n.startswith("xl/pivotTables/")),
        ("macro", lambda n: n.endswith("vbaProject.bin")),
        ("external_link", lambda n: n.startswith("xl/externalLinks/")),
        ("slicer", lambda n: n.startswith("xl/slicers/")),
    ):
        if count := sum(1 for n in names if test(n)):
            out.append(f"{label}:{count}")
    z = sheet_guard.open_zip(data)
    for n in names:
        if n.startswith("xl/drawings/drawing") and n.endswith(".xml"):
            if re.search(rb"<(?:\w+:)?sp[ >]", z.read(n)):
                out.append("shape")
        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"):
            body = z.read(n)
            if b"sparklineGroup" in body:
                out.append("sparkline")
            if b"x14:conditionalFormatting" in body:
                out.append("x14_conditional_format")
    return sorted(set(out))


def to_model(data: bytes) -> dict[str, Any]:
    sheet_guard.check(data)
    formulas = openpyxl.load_workbook(io.BytesIO(data))
    values = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    sheets: list[dict[str, Any]] = []
    budget = MAX_CELLS
    truncated = False
    for ws in formulas.worksheets:
        wv: Worksheet = values[ws.title]
        cells: list[dict[str, Any]] = []
        for row in ws.iter_rows():
            for cell in row:
                style = _style(cell)
                raw = cell.value
                if raw is None and not style:
                    continue
                if budget <= 0:
                    truncated = True
                    break
                budget -= 1
                item: dict[str, Any] = {"r": cell.row - 1, "c": cell.column - 1}
                if isinstance(raw, str) and raw.startswith("=") and cell.data_type == "f":
                    item["f"] = raw[1:]
                    shown = wv.cell(cell.row, cell.column).value
                    if (serial := _serial(shown)) is not None:
                        shown = serial
                    if isinstance(shown, (int, float, str, bool)):
                        item["v"] = shown
                elif raw is not None:
                    serial = _serial(raw)
                    item["v"] = serial if serial is not None else raw
                    if serial is not None and "numFmt" not in style:
                        style["numFmt"] = "yyyy-mm-dd"
                if style:
                    item["s"] = style
                cells.append(item)
        sheet: dict[str, Any] = {
            "name": ws.title,
            "rows": max(ws.max_row, 1),
            "cols": max(ws.max_column, 1),
            "cells": cells,
            "merges": sorted(str(r) for r in ws.merged_cells.ranges),
            "colWidths": {
                str(openpyxl.utils.column_index_from_string(k) - 1): round(d.width * 7 + 5)
                for k, d in ws.column_dimensions.items() if d.width and not d.hidden
            },
            "rowHeights": {
                str(k - 1): round(d.height * 96 / 72)
                for k, d in ws.row_dimensions.items() if d.height
            },
        }
        if ws.sheet_state != "visible":
            sheet["hidden"] = True
        if ws.freeze_panes:
            cell = ws[ws.freeze_panes]
            sheet["freeze"] = {"r": cell.row - 1, "c": cell.column - 1}
        sheets.append(sheet)
    return {"sheets": sheets, "kept": kept(data), "truncated": truncated}
