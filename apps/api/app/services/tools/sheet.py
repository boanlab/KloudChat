"""Sheet tools: read the conversation's workbook, change it in place, or write a new one
(`office.py`), against the saved `.xlsx` (`services/sheet_edit.py`). A formula is written as a
formula, and the editor beside the chat shows what the tools saved."""

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from app.core.db import SessionLocal
from app.models.workspace import StoredFile
from app.services import files, sheet_build, sheet_doc
from app.services import sheet_edit as edits
from app.services.tools.base import ToolContext
from app.services.tools.office import Toolset, tools_for

__all__ = ["NAMES", "TOOLS", "xlsx_edit", "xlsx_read", "xlsx_restore", "xlsx_write"]

_CELL = (
    "A cell is a number, a text, true/false, null (empty), or a text starting with `=` for a "
    "formula (`=SUM(B2:B9)`, `=B2/C2`, `='1월'!D5*2`): use a formula wherever a value follows "
    "from other cells (a total, a difference, a rate, price × quantity), so the sheet stays "
    "live when the person edits it; guard a formula over a cell that may still hold a bracket "
    "blank or be empty (`=IF(ISNUMBER(B5),B5-B4,\"\")`). A number is a JSON number (1200, "
    "0.125), never a text like 「1,200원」; a text of the form 「1,200」, 「12.5%」 or 「2026-10-13」 is stored as that "
    "number with its format."
)
_NEVER = (
    "Never invent a number, date, name or fact: a value the person or their material did not "
    "give is not typed in. Put a bracket blank text named for it (「[매출액]」, 「[담당자]」) "
    "in the cell and say what is missing; a figure that follows from given cells is a formula, "
    "never a number you worked out and typed. The tool reports the values the formulas give: "
    "quote totals and rates only from that report (or `xlsx_read`)."
)

#: an attached file `from_file` reads at most this big
MAX_SOURCE = 15 * 1024 * 1024


async def _prepare(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """A sheet given as `from_file` (an attached CSV or Excel file, by name or id) gets its rows
    from that file as written, so a big table is never retyped by the model."""
    sheets = args.get("sheets")
    wanted = {
        str((s.get("from_file") or {}).get("file") or "").strip()
        for s in (sheets if isinstance(sheets, list) else [])
        if isinstance(s, dict) and isinstance(s.get("from_file"), dict)
    }
    wanted.discard("")
    if not wanted:
        return args
    async with SessionLocal() as db:
        rows = (await db.exec(
            select(StoredFile)
            .where(StoredFile.user_id == ctx.user_id,
                   (col(StoredFile.id).in_(wanted)) | (col(StoredFile.name).in_(wanted)))
            .order_by(col(StoredFile.created_at).desc())
        )).all()
    found: dict[str, StoredFile] = {}
    for row in rows:
        # the conversation's own files first: a name shared with another chat's file
        if ctx.session_id and row.session_id not in (None, ctx.session_id):
            continue
        for key in (row.id, row.name):
            if key in wanted and key not in found:
                found[key] = row
    notes: list[str] = []
    out: list[Any] = []
    for spec in sheets:
        source = spec.get("from_file") if isinstance(spec, dict) else None
        if not isinstance(source, dict) or not str(source.get("file") or "").strip():
            out.append(spec)
            continue
        name = str(source["file"]).strip()
        row = found.get(name)
        if row is None or not row.storage_key:
            raise edits.SheetError(
                f"첨부 파일 「{name}」을 이 대화에서 찾지 못했습니다. "
                "올린 파일 이름을 그대로 적으세요.")
        if row.size > MAX_SOURCE:
            raise edits.SheetError(f"「{name}」이 너무 큽니다(15MB까지).")
        try:
            data = files.read_blob(row.storage_key)
        except OSError:
            raise edits.SheetError(f"「{name}」 파일을 읽지 못했습니다.") from None
        cells = sheet_build.rows_from_file(row.name, data, str(source.get("sheet") or ""))
        if not cells:
            raise edits.SheetError(f"「{name}」에 옮길 내용이 없습니다.")
        notes.append(f"「{row.name}」에서 {len(cells)}행 × {max(len(r) for r in cells)}열을 "
                     "있는 그대로 옮겼습니다(모르는 칸은 비워 둠).")
        out.append({**{k: v for k, v in spec.items() if k != "from_file"}, "rows": cells})
    return {**args, "sheets": out, "__notes__": notes}


TOOLS = tools_for(Toolset(
    prefix="xlsx",
    label="시트",
    doc=sheet_doc,
    edit=edits,
    error=edits.SheetError,
    # looked up per run, so a test can swap the database
    session=lambda: SessionLocal(),
    ops=edits.OPS,
    renumbers=edits.RENUMBERS,
    read_text=(
        "Read the sheet (Excel workbook) open beside this chat, as the person last saved it in "
        "the editor. Returns each sheet's size, column widths, merges and number formats, then "
        "every non-empty row as `A2=1월 | B2=1200 | C2 =B2*0.1 → 120`: a plain cell is "
        "`address=value`, a formula is `address =formula → the value it gives`. Bracket blanks "
        "(「[담당자]」) still in the cells are listed at the end. A big sheet is cut; read the "
        "rest with `sheet` and `range` (`A41:F90`, `C:C`, `3:3`). Call it before every "
        "xlsx_edit: the addresses belong to this version."
    ),
    read_params={
        "sheet": {"type": "string", "description": "시트 이름: 그 시트만 읽을 때"},
        "range": {"type": "string", "description": "읽을 범위: A41:F90, C:C, 3:3"},
    },
    edit_text=(
        "Change the open sheet in place; formulas, formats, merges, widths, charts and the "
        "rest of the file stay as they are. Ops apply in order and all together or none do "
        "(addresses are those after the ops before them). Rows count from 1, columns are "
        "letters, a sheet is named in `sheet` (leave it out only when there is one sheet). "
        "Ops: set_cells {sheet, at:'B2', values:[[…],[…]]} writes a block of cells from `at` "
        "(a single value or one row is fine; null empties a cell) · clear {sheet, range} · "
        "format {sheet, range:'A1:D1', numFmt?:'#,##0'|'0.0%'|'yyyy-mm-dd', bold?, italic?, "
        "underline?, size?, color?:'#RRGGBB', fill?:'#RRGGBB'|'none', align?:left|center|"
        "right, valign?:top|center|bottom, wrap?, border?:all|outline|top|bottom|none, "
        "border_style?:thin|medium|thick|dashed|dotted|double} · width {sheet, col:'B' or "
        "cols:'A:D', px} · merge / unmerge {sheet, range} · insert_rows / delete_rows {sheet, "
        "at:5, count?} (at: the row to insert before, or the first row to delete) · "
        "insert_cols / delete_cols {sheet, at:'C', count?} (formulas that name moved cells "
        "follow them) · add_sheet {name, position?} · rename_sheet {sheet, name} (formulas "
        "follow) · delete_sheet {sheet}. " + _CELL + " " + _NEVER
        + " Never delete or overwrite what the person typed unless they asked for exactly "
        "that; ask first. It cannot make charts, pivot tables or conditional formats: say so."
    ),
    edit_items={
        "type": "object",
        # where, then what: a model that writes keys in this order ends a set_cells at `at`
        "properties": {
            "op": {"type": "string", "enum": list(edits.OPS)},
            "sheet": {"type": "string"},
            "at": {"type": ["string", "integer"]},
            "range": {"type": "string"},
            "values": {"type": "array", "items": {"type": "array", "items": {}}},
            "name": {"type": "string"},
            "position": {"type": "integer"},
            "count": {"type": "integer"},
            "col": {"type": "string"},
            "cols": {"type": "string"},
            "px": {"type": "integer"},
            "numFmt": {"type": "string"},
            "bold": {"type": "boolean"},
            "italic": {"type": "boolean"},
            "underline": {"type": "boolean"},
            "size": {"type": "number"},
            "color": {"type": "string"},
            "fill": {"type": "string"},
            "align": {"type": "string", "enum": ["left", "center", "right"]},
            "valign": {"type": "string", "enum": ["top", "center", "bottom"]},
            "wrap": {"type": "boolean"},
            "border": {"type": "string",
                       "enum": ["all", "outline", "top", "bottom", "none"]},
            "border_style": {"type": "string"},
        },
        "required": ["op"],
    },
    write_text=(
        "Write a new sheet (Excel workbook) and open it in the editor beside the chat. Use it "
        "when the person asks for a new spreadsheet, a table as an Excel file, or a table with "
        "calculations; to change the open one use xlsx_read and xlsx_edit. `sheets`: each "
        "{name, rows:[[…],[…]], widths?, styles?, merges?, freeze?, chart?}; the first row of "
        "`rows` is the header (bold, shaded, frozen) unless header:false. For a table in an "
        "attached CSV or Excel file give from_file:{file:'<its name>', sheet?} instead of "
        "`rows`: the rows are copied from the file as written (use it for anything past a "
        "few dozen rows; add the formulas afterwards with xlsx_edit). " + _CELL + " Put the "
        "computed columns and the 합계 row in as formulas over the cells they follow from. "
        "widths: {'A':120,'C:D':90} in px (leave out and the widths fit the text). styles: "
        "[{range:'B2:B20', numFmt:'#,##0'|'0.0%'|'yyyy-mm-dd', bold?, fill?, color?, align?, "
        "border?}]: a ratio column needs '0.0%', an amount '#,##0'. The cell limit is 50000 "
        "per workbook; add more later with xlsx_edit. chart: {type:'bar'|'line'|'stacked', "
        "x:'월', y:['매출','비용'], title?} puts a native Excel chart beside the table, its "
        "columns named by header text or letter; only when the person asked for a chart. "
        + _NEVER + " A table the person pasted "
        "or attached goes in cell for cell as written: no rounding, no unit change, nothing "
        "filled in; a missing cell is null. Reply with the sheets, row counts and which "
        "columns are formulas in a few lines: do not paste the table back."
    ),
    write_params={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "sheets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "rows": {"type": "array", "items": {"type": "array", "items": {}}},
                        "from_file": {
                            "type": "object",
                            "properties": {"file": {"type": "string"}, "sheet": {"type": "string"}},
                            "required": ["file"],
                        },
                        "chart": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["bar", "line", "stacked"]},
                                "x": {"type": "string"},
                                "y": {"type": "array", "items": {"type": "string"}},
                                "title": {"type": "string"},
                            },
                            "required": ["x", "y"],
                        },
                        "header": {"type": "boolean"},
                        "widths": {"type": "object", "additionalProperties": {"type": "integer"}},
                        "styles": {"type": "array", "items": {"type": "object"}},
                        "merges": {"type": "array", "items": {"type": "string"}},
                        "freeze": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["title", "sheets"],
    },
    build=lambda args, _look: sheet_build.build(args),
    prepare=_prepare,
    report=edits.report,
))
XLSX_READ, XLSX_EDIT, XLSX_WRITE, XLSX_RESTORE = TOOLS
xlsx_read, xlsx_edit, xlsx_write, xlsx_restore = (tool.run for tool in TOOLS)
NAMES = frozenset(tool.name for tool in TOOLS)
