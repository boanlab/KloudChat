"""What the agent's sheet tools do to an `.xlsx`: read it as text, and change it by ops written the
way a person talks about a sheet (A1 addresses, 1-based rows), applied through `sheet_patch`, the
same writer the editor's saves use. A formula stays a formula; the values the formulas give are
calculated again after the ops (`sheet_calc`) so a file read without a calculation engine is true.

Ops apply in order and all together: one that fails stops the call and nothing is saved.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import openpyxl
from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils import column_index_from_string, get_column_letter

from app.services import sheet_calc, sheet_model, sheet_patch

log = logging.getLogger(__name__)

OPS = (
    "set_cells", "clear", "format", "width", "merge", "unmerge", "insert_rows", "delete_rows",
    "insert_cols", "delete_cols", "add_sheet", "rename_sheet", "delete_sheet",
)
#: ops after which the addresses the person (and the model) read are no longer the same
RENUMBERS = frozenset({"insert_rows", "delete_rows", "insert_cols", "delete_cols", "add_sheet",
                       "rename_sheet", "delete_sheet"})
MAX_OPS = 200
#: cells one call may write or style
MAX_CELLS = 20_000
#: what `outline` shows of a workbook at most
OUTLINE_CHARS = 16_000
RANGE_CHARS = 40_000
MAX_ROWS = 1_048_576
MAX_COLS = 16_384


class SheetError(ValueError):
    pass


@dataclass
class Result:
    data: bytes
    changes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# -- addresses -------------------------------------------------------------------------------

_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")
_COLS = re.compile(r"^\$?([A-Za-z]{1,3})(?::\$?([A-Za-z]{1,3}))?$")
_ROWS = re.compile(r"^\$?(\d{1,7})(?::\$?(\d{1,7}))?$")


def col_of(text: Any) -> int:
    """A column as a 0-based index: `C`, or a number counted from 1."""
    if isinstance(text, int) and not isinstance(text, bool):
        if text < 1:
            raise SheetError(f"열 번호는 1부터입니다: {text}")
        return text - 1
    m = re.fullmatch(r"\$?([A-Za-z]{1,3})", str(text or "").strip())
    if m is None:
        raise SheetError(f"열은 A, B, C처럼 적으세요: {text!r}")
    index = column_index_from_string(m.group(1).upper()) - 1
    if index >= MAX_COLS:
        raise SheetError(f"열이 범위를 넘었습니다: {text}")
    return index


def row_of(value: Any) -> int:
    """A row as a 0-based index: the number the person sees, counted from 1."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SheetError(f"행은 1부터 시작하는 번호로 적으세요: {value!r}") from None
    if not 1 <= number <= MAX_ROWS:
        raise SheetError(f"행 번호가 범위를 벗어났습니다: {value}")
    return number - 1


def cell_of(text: Any) -> tuple[int, int]:
    m = _CELL.match(str(text or "").strip())
    if m is None:
        raise SheetError(f"칸은 B2처럼 적으세요: {text!r}")
    row = row_of(m.group(2))
    return row, col_of(m.group(1))


def range_of(text: Any, rows: int, cols: int) -> tuple[int, int, int, int]:
    """(row1, col1, row2, col2), 0-based and inclusive, of `B2:D9`, `B2`, `C:C` or `3:3`
    (a whole column or row reaches the sheet's last used one)."""
    raw = str(text or "").strip()
    if not raw:
        raise SheetError("range가 비어 있습니다.")
    if _CELL.match(raw) or ":" in raw and all(_CELL.match(p) for p in raw.split(":")):
        a, _, b = raw.partition(":")
        r1, c1 = cell_of(a)
        r2, c2 = cell_of(b or a)
    elif m := _COLS.match(raw):
        c1, c2 = col_of(m.group(1)), col_of(m.group(2) or m.group(1))
        r1, r2 = 0, max(rows, 1) - 1
    elif m := _ROWS.match(raw):
        r1, r2 = row_of(m.group(1)), row_of(m.group(2) or m.group(1))
        c1, c2 = 0, max(cols, 1) - 1
    else:
        raise SheetError(f"범위는 B2:D9, C:C, 3:3처럼 적으세요: {text!r}")
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def address(r: int, c: int) -> str:
    return f"{get_column_letter(c + 1)}{r + 1}"


def span(box: tuple[int, int, int, int]) -> str:
    r1, c1, r2, c2 = box
    return address(r1, c1) if (r1, c1) == (r2, c2) else f"{address(r1, c1)}:{address(r2, c2)}"


# -- values ----------------------------------------------------------------------------------

#: functions that reach outside the workbook or run something: a formula the agent writes never
#: uses them (a sheet's text can come from a page or a file the person never read)
_UNSAFE = re.compile(
    r"\b(?:WEBSERVICE|FILTERXML|IMPORT\w*|CALL|REGISTER(?:\.ID)?|EXEC|SHELL|HYPERLINK|RTD|DDE"
    r"|ENCODEURL|INFO|CELL)\s*\(|\[[^\]]*\]|\|", re.I)
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_GROUPED = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.(\d+))?$")
_PERCENT = re.compile(r"^(-?\d+(?:\.(\d+))?)\s?%$")
_EPOCH = dt.date(1899, 12, 30)
MAX_TEXT = 32_000


def _well_formed(text: str) -> bool:
    """The formula's tokens close their brackets and neither start nor end on an operator, so a
    text that only begins with `=` (「=== 구분 ===」) is not written as a formula Excel cannot
    open."""
    try:
        items = [t for t in Tokenizer(text).items if t.type != Token.WSPACE]
    except Exception:  # noqa: BLE001 - the tokenizer's own error says nothing the model can use
        return False
    depth = 0
    for item in items:
        if item.subtype == Token.OPEN:
            depth += 1
        elif item.subtype == Token.CLOSE:
            depth -= 1
            if depth < 0:
                return False
    return bool(items) and depth == 0 and items[0].type != Token.OP_IN \
        and items[-1].type not in (Token.OP_IN, Token.OP_PRE, Token.SEP)


def _formula_ok(text: str) -> str:
    body = text[1:]
    if not _well_formed(text):
        raise SheetError(
            f"「{text[:60]}」은 수식으로 읽을 수 없습니다. 수식은 =SUM(B2:B9)처럼 괄호가 닫히고 "
            "연산자로 끝나지 않아야 하고, 글자를 = 로 시작하려면 앞에 작은따옴표나 공백을 두세요.")
    # a quoted string may hold anything: only what is outside quotes is checked
    outside = "".join(re.split(r'("(?:[^"]|"")*")', body)[0::2])
    if _UNSAFE.search(outside):
        raise SheetError(
            f"수식 「{text[:60]}」은 쓸 수 없습니다(외부 파일·주소를 부르거나 무언가를 "
            "실행하는 함수, 다른 통합 문서 참조).")
    return body


def typed(value: Any) -> tuple[Any, str | None]:
    """A value as the cell holds it, and the number format it implies. A text that is a number
    written as people write it (「1,200」, 「12.5%」, 「2026-10-13」) is the number; a plain
    digit string (a code, a year label) stays text."""
    if value is None or isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            raise SheetError("숫자가 아닌 값(NaN, 무한대)은 넣을 수 없습니다.")
        return value, None
    if not isinstance(value, str):
        raise SheetError(f"칸에는 글자·숫자·참거짓·수식만 넣을 수 있습니다: {value!r}")
    text = value[:MAX_TEXT]
    if m := _DATE.match(text.strip()):
        try:
            day = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return text, None
        return float((day - _EPOCH).days), "yyyy-mm-dd"
    if m := _GROUPED.match(text.strip()):
        number = float(text.replace(",", ""))
        digits = len(m.group(1) or "")
        return (int(number) if not digits else number), "#,##0" + ("." + "0" * digits
                                                                   if digits else "")
    if m := _PERCENT.match(text.strip()):
        digits = len(m.group(2) or "")
        return round(float(m.group(1)) / 100, digits + 2), "0" + ("." + "0" * digits
                                                                   if digits else "") + "%"
    return text, None


def _cell_ops(sheet: str, r: int, c: int, value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value.startswith("=") and len(value) > 1:
        return {"sheet": sheet, "r": r, "c": c, "f": _formula_ok(value)}
    number, fmt = typed(value)
    op: dict[str, Any] = {"sheet": sheet, "r": r, "c": c, "v": number}
    if fmt:
        op["s"] = {"numFmt": fmt}
    return op


# -- the workbook ----------------------------------------------------------------------------

def _dims(data: bytes) -> dict[str, tuple[int, int]]:
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    try:
        return {ws.title: (max(ws.max_row or 1, 1), max(ws.max_column or 1, 1))
                for ws in book.worksheets}
    finally:
        book.close()


def _sheet_name(name: Any, names: list[str]) -> str:
    wanted = " ".join(str(name or "").split())
    if not wanted:
        if len(names) == 1:
            return names[0]
        raise SheetError(f"어느 시트인지 sheet에 적으세요: {', '.join(f'「{n}」' for n in names)}")
    for candidate in names:
        if candidate == wanted:
            return candidate
    for candidate in names:
        if candidate.casefold() == wanted.casefold():
            return candidate
    raise SheetError(f"시트 「{wanted}」이 없습니다. 있는 시트: "
                     + ", ".join(f"「{n}」" for n in names))


def _patch(data: bytes, ops: list[dict[str, Any]]) -> bytes:
    try:
        return sheet_patch.apply(data, ops)
    except sheet_patch.PatchError as error:
        code, _, arg = str(error).partition(":")
        text = {
            "no_sheet": f"시트 「{arg}」이 없습니다.",
            "bad_sheet_name": f"시트 이름으로 쓸 수 없습니다(31자까지, [ ] : * ? / \\ 불가): {arg}",
            "sheet_exists": f"같은 이름의 시트가 이미 있습니다: {arg}",
            "last_sheet": "마지막 시트는 지울 수 없습니다.",
            "bad_range": "행·열 위치나 개수가 올바르지 않습니다.",
        }.get(code, str(error))
        raise SheetError(text) from None


# -- formulas follow moved lines -------------------------------------------------------------

_OPERAND = re.compile(r"^(?:(?P<sheet>'(?:[^']|'')+'|[^'!]+)!)?(?P<a>[^:!]+)(?::(?P<b>[^:!]+))?$")
_END = re.compile(r"^(\$?)([A-Za-z]{1,3})?(\$?)(\d{1,7})?$")


def _shift_operand(value: str, home: str, target: str, axis: str, at: int, count: int,
                   delete: bool) -> str:
    m = _OPERAND.match(value)
    if m is None:
        return value
    sheet = m.group("sheet")
    name = sheet[1:-1].replace("''", "'") if sheet and sheet.startswith("'") else (sheet or home)
    if name.casefold() != target.casefold():
        return value
    ends = [e for e in (m.group("a"), m.group("b")) if e]
    parsed = []
    for end in ends:
        part = _END.match(end)
        if part is None or not (part.group(2) or part.group(4)):
            return value
        parsed.append(part)
    index = []
    for part in parsed:
        if axis == "row":
            index.append(int(part.group(4)) - 1 if part.group(4) else None)
        else:
            index.append(column_index_from_string(part.group(2).upper()) - 1
                         if part.group(2) else None)
    if any(i is None for i in index):
        return value
    lo, hi = min(index), max(index)
    mapped = sheet_patch.map_span(lo, hi, at, count, delete)
    if mapped is None:
        return "#REF!"
    new = [mapped[0] if i == lo else mapped[1] for i in index] if len(index) == 2 \
        else [mapped[0]]
    if len(index) == 2 and index[0] > index[1]:
        new = [mapped[1], mapped[0]]
    out = []
    for part, i in zip(parsed, new, strict=True):
        if axis == "row":
            out.append(f"{part.group(1)}{part.group(2) or ''}{part.group(3)}{i + 1}")
        else:
            out.append(f"{part.group(1)}{get_column_letter(i + 1)}{part.group(3)}"
                       f"{part.group(4) or ''}")
    return (f"{sheet}!" if sheet else "") + ":".join(out)


def shift_refs(formula: str, home: str, target: str, axis: str, at: int, count: int,
               delete: bool) -> str:
    """`formula` (written in sheet `home`) with every reference into sheet `target` moved for
    `count` rows or columns (`axis`) inserted at, or deleted from, `at` (0-based)."""
    try:
        tokens = Tokenizer("=" + formula)
    except Exception:  # noqa: BLE001 - a text the tokenizer cannot read is left as it is
        return formula
    changed = False
    for token in tokens.items:
        if token.type == Token.OPERAND and token.subtype == Token.RANGE:
            new = _shift_operand(token.value, home, target, axis, at, count, delete)
            if new != token.value:
                token.value = new
                changed = True
    return tokens.render()[1:] if changed else formula


def _drop_sheet_refs(formula: str, home: str, gone: str) -> str:
    try:
        tokens = Tokenizer("=" + formula)
    except Exception:  # noqa: BLE001
        return formula
    changed = False
    for token in tokens.items:
        if token.type == Token.OPERAND and token.subtype == Token.RANGE and "!" in token.value:
            sheet = token.value.rpartition("!")[0]
            name = sheet[1:-1].replace("''", "'") if sheet.startswith("'") else sheet
            if name.casefold() == gone.casefold():
                token.value = "#REF!"
                changed = True
    return tokens.render()[1:] if changed else formula


# -- styles ----------------------------------------------------------------------------------

_COLOR = re.compile(r"^#?[0-9A-Fa-f]{6}$")
_BORDER_STYLES = ("thin", "medium", "thick", "dashed", "dotted", "double", "hair")


def _color(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _COLOR.match(value.strip()):
        raise SheetError(f"{what}은 #RRGGBB 색으로 적으세요: {value!r}")
    return "#" + value.strip().lstrip("#").upper()


def _style_of(op: dict[str, Any]) -> dict[str, Any]:
    style: dict[str, Any] = {}
    if "numFmt" in op:
        style["numFmt"] = str(op["numFmt"])[:80] or "General"
    font: dict[str, Any] = {}
    for key, flag in (("bold", "b"), ("italic", "i"), ("underline", "u"), ("strike", "s")):
        if key in op:
            font[flag] = bool(op[key])
    if "size" in op:
        try:
            size = float(op["size"])
        except (TypeError, ValueError):
            raise SheetError(f"size는 글자 크기(pt)입니다: {op['size']!r}") from None
        if not 4 <= size <= 72:
            raise SheetError("size는 4~72 사이로 적으세요.")
        font["sz"] = size
    if "color" in op:
        font["color"] = _color(op["color"], "color")
    if "font" in op:
        font["name"] = str(op["font"])[:60]
    if font:
        style["font"] = font
    if "fill" in op:
        style["fill"] = "" if op["fill"] in (None, "", "none") else _color(op["fill"], "fill")
    align: dict[str, Any] = {}
    if "align" in op:
        if op["align"] not in ("left", "center", "right"):
            raise SheetError("align은 left, center, right 중 하나입니다.")
        align["h"] = op["align"]
    if "valign" in op:
        if op["valign"] not in ("top", "center", "bottom"):
            raise SheetError("valign은 top, center, bottom 중 하나입니다.")
        align["v"] = op["valign"]
    if "wrap" in op:
        align["wrap"] = bool(op["wrap"])
    if align:
        style["align"] = align
    return style


def _borders(op: dict[str, Any], box: tuple[int, int, int, int], r: int, c: int) -> dict[str, Any]:
    """The border sides cell (r, c) of `box` gets: `all`, `outline`, `top`, `bottom` or `none`."""
    mode = op.get("border")
    if mode is None:
        return {}
    style = str(op.get("border_style") or "thin")
    if style not in _BORDER_STYLES:
        raise SheetError(f"border_style은 {', '.join(_BORDER_STYLES)} 중 하나입니다.")
    color = _color(op.get("border_color") or "#000000", "border_color")
    line = {"style": style, "color": color}
    r1, c1, r2, c2 = box
    sides = {
        "all": {"t": line, "b": line, "l": line, "r": line},
        "outline": {**({"t": line} if r == r1 else {}), **({"b": line} if r == r2 else {}),
                    **({"l": line} if c == c1 else {}), **({"r": line} if c == c2 else {})},
        "top": {"t": line} if r == r1 else {},
        "bottom": {"b": line} if r == r2 else {},
        "none": {"t": None, "b": None, "l": None, "r": None},
    }
    if mode not in sides:
        raise SheetError("border는 all, outline, top, bottom, none 중 하나입니다.")
    return sides[mode]


# -- applying ops ----------------------------------------------------------------------------

def _names(data: bytes) -> list[str]:
    return list(_dims(data))


_LINES = {"insert_rows": "insertRows", "delete_rows": "deleteRows",
          "insert_cols": "insertCols", "delete_cols": "deleteCols"}


def _count(box: tuple[int, int, int, int]) -> int:
    return (box[2] - box[0] + 1) * (box[3] - box[1] + 1)


def _grid(values: Any) -> list[list[Any]]:
    if not isinstance(values, list):
        return [[values]]
    if values and all(not isinstance(v, list) for v in values):
        return [values]
    if not all(isinstance(row, list) for row in values):
        raise SheetError("values는 행의 목록입니다: [[칸, 칸], [칸, 칸]]")
    return values


def apply(data: bytes, ops: list[dict[str, Any]]) -> Result:
    if not isinstance(ops, list) or not ops:
        raise SheetError("ops가 비어 있습니다.")
    if len(ops) > MAX_OPS:
        raise SheetError(f"한 번에 {MAX_OPS}개 op까지 보낼 수 있습니다.")
    changes: list[str] = []
    notes: list[str] = []
    out = data
    for number, op in enumerate(ops, 1):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise SheetError(f"op {number}: op는 {', '.join(OPS)} 중 하나입니다.")
        try:
            out = _one(out, op, changes, notes)
        except SheetError as error:
            raise SheetError(f"op {number}({op.get('op')}): {error}") from None
        except Exception as error:  # noqa: BLE001 - a package the writers cannot walk
            log.warning("sheet op %s failed: %s", op.get("op"), error, exc_info=True)
            raise SheetError(f"op {number}({op.get('op')}): 이 파일에는 이 작업을 할 수 "
                             "없습니다.") from None
    return Result(sheet_calc.refresh(out), changes, notes)


def _one(data: bytes, op: dict[str, Any], changes: list[str], notes: list[str]) -> bytes:
    kind = op["op"]
    dims = _dims(data)
    names = list(dims)
    if kind == "add_sheet":
        name = " ".join(str(op.get("name") or "").split())
        position = op.get("position")
        index = None if position is None else max(int(position) - 1, 0)
        out = _patch(data, [{"op": "addSheet", "name": name, "index": index}])
        changes.append(f"시트 「{name}」 추가")
        return out
    sheet = _sheet_name(op.get("sheet"), names)
    rows, cols = dims[sheet]
    if kind == "rename_sheet":
        name = " ".join(str(op.get("name") or "").split())
        out = _patch(data, [{"op": "renameSheet", "sheet": sheet, "name": name}])
        changes.append(f"시트 「{sheet}」 이름을 「{name}」으로 바꿈(수식의 참조도 따라감)")
        return out
    if kind == "delete_sheet":
        out = _patch(data, [{"op": "deleteSheet", "sheet": sheet}])
        out = sheet_patch.map_formulas(
            out, lambda text, home: _drop_sheet_refs(text, home, sheet))
        changes.append(f"시트 「{sheet}」 삭제")
        notes.append(f"「{sheet}」를 가리키던 수식은 #REF!가 됩니다: 필요하면 다시 쓰세요.")
        return out
    if kind == "set_cells":
        r0, c0 = cell_of(op.get("at"))
        grid = _grid(op.get("values"))
        size = sum(len(row) for row in grid)
        if size == 0:
            raise SheetError("values가 비어 있습니다.")
        if size > MAX_CELLS:
            raise SheetError(f"한 번에 {MAX_CELLS}칸까지 쓸 수 있습니다.")
        if r0 + len(grid) > MAX_ROWS or c0 + max(len(r) for r in grid) > MAX_COLS:
            raise SheetError("시트 범위를 넘습니다.")
        cell_ops = [_cell_ops(sheet, r0 + i, c0 + j, value)
                    for i, row in enumerate(grid) for j, value in enumerate(row)]
        formulas = sum(1 for o in cell_ops if "f" in o)
        out = _patch(data, cell_ops)
        box = (r0, c0, r0 + len(grid) - 1, c0 + max(len(r) for r in grid) - 1)
        changes.append(f"「{sheet}」 {span(box)}에 {size}칸 씀"
                       + (f"(수식 {formulas}칸)" if formulas else ""))
        return out
    if kind in ("merge", "unmerge", "clear", "format"):
        box = range_of(op.get("range"), rows, cols)
        if kind in ("merge", "unmerge"):
            if _count(box) < 2 and kind == "merge":
                raise SheetError("병합은 두 칸 이상의 범위입니다.")
            out = _patch(data, [{"op": kind, "sheet": sheet, "ref": span(box)}])
            changes.append(f"「{sheet}」 {span(box)} {'병합' if kind == 'merge' else '병합 해제'}")
            return out
        if _count(box) > MAX_CELLS:
            raise SheetError(f"한 번에 {MAX_CELLS}칸까지 다룰 수 있습니다.")
        if kind == "clear":
            cell_ops = [{"sheet": sheet, "r": r, "c": c, "v": None}
                        for r in range(box[0], box[2] + 1) for c in range(box[1], box[3] + 1)]
            changes.append(f"「{sheet}」 {span(box)} 비움")
            return _patch(data, cell_ops)
        base = _style_of(op)
        if not base and "border" not in op:
            raise SheetError("format에는 numFmt, bold, fill, color, align, border 같은 "
                             "서식 항목이 하나는 있어야 합니다.")
        cell_ops = []
        for r in range(box[0], box[2] + 1):
            for c in range(box[1], box[3] + 1):
                style = dict(base)
                if side := _borders(op, box, r, c):
                    style["border"] = side
                if style:
                    cell_ops.append({"sheet": sheet, "r": r, "c": c, "s": style})
        changes.append(f"「{sheet}」 {span(box)} 서식 바꿈")
        return _patch(data, cell_ops)
    if kind == "width":
        spec = op.get("cols") or op.get("col")
        if spec is None:
            raise SheetError("width에는 col(B) 또는 cols(A:D)가 있어야 합니다.")
        first, _, last = str(spec).partition(":")
        c1, c2 = col_of(first), col_of(last or first)
        try:
            px = int(op.get("px"))
        except (TypeError, ValueError):
            raise SheetError("px는 열 너비(화면 픽셀)입니다.") from None
        if not 20 <= px <= 800:
            raise SheetError("px는 20~800 사이로 적으세요.")
        out = _patch(data, [{"op": "width", "sheet": sheet, "c": c, "px": px}
                            for c in range(min(c1, c2), max(c1, c2) + 1)])
        changes.append(f"「{sheet}」 {get_column_letter(min(c1, c2) + 1)}"
                       f"{'' if c1 == c2 else ':' + get_column_letter(max(c1, c2) + 1)}"
                       f"열 너비 {px}px")
        return out
    # inserting and deleting lines
    rows_axis = kind.endswith("rows")
    delete = kind.startswith("delete")
    at = row_of(op.get("at")) if rows_axis else col_of(op.get("at"))
    try:
        count = int(op.get("count", 1))
    except (TypeError, ValueError):
        raise SheetError("count는 개수입니다.") from None
    if not 1 <= count <= 1000:
        raise SheetError("count는 1~1000 사이로 적으세요.")
    axis = "row" if rows_axis else "col"
    out = _patch(data, [{"op": _LINES[kind], "sheet": sheet, "at": at, "count": count}])
    out = sheet_patch.map_formulas(
        out, lambda text, home: shift_refs(text, home or sheet, sheet, axis, at, count, delete))
    label = (f"{at + 1}행" if rows_axis else f"{get_column_letter(at + 1)}열")
    changes.append(f"「{sheet}」 {label}부터 {'행' if rows_axis else '열'} {count}개 "
                   f"{'삭제' if delete else '끼움'}(아래·오른쪽 칸과 수식의 참조가 따라감)")
    return out


# -- reading ---------------------------------------------------------------------------------

def _shown(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, float):
        return repr(round(value, 10))
    text = str(value).replace("\r", "").replace("\n", "\\n")
    return text if len(text) <= 160 else text[:157] + "…"


def _cell_text(cell: dict[str, Any]) -> str:
    where = address(cell["r"], cell["c"])
    if "f" in cell:
        calc = f" → {_shown(cell['v'])}" if "v" in cell else " → (값 없음)"
        return f"{where} ={cell['f']}{calc}"
    return f"{where}={_shown(cell['v'])}"


def _formats(cells: list[dict[str, Any]]) -> list[str]:
    seen: dict[tuple[int, str], list[int]] = {}
    for cell in cells:
        fmt = (cell.get("s") or {}).get("numFmt")
        if fmt and fmt != "General" and "v" in cell:
            seen.setdefault((cell["c"], fmt), []).append(cell["r"])
    return [f"{address(min(rs), c)}:{address(max(rs), c)} {fmt}"
            for (c, fmt), rs in sorted(seen.items())][:12]


def outline(data: bytes, sheet: Any = None, range: Any = None) -> str:  # noqa: A002
    """The workbook as text for the model: per sheet its size, widths, merges, number formats and
    each non-empty row as `A2=1월 | B2=1200 | C2 =B2*0.1 → 120` (a formula, then the value it
    gives). `sheet` and `range` read just that part, with room for more of it."""
    model = sheet_model.to_model(data)
    sheets = model["sheets"]
    names = [s["name"] for s in sheets]
    if sheet:
        sheets = [s for s in sheets if s["name"] == _sheet_name(sheet, names)]
    budget = RANGE_CHARS if range else OUTLINE_CHARS
    lines = [f"시트 {len(names)}개: " + ", ".join(f"「{n}」" for n in names)]
    for item in sheets:
        box = range_of(range, item["rows"], item["cols"]) if range else None
        cells = [c for c in item["cells"] if "v" in c or "f" in c]
        if box:
            cells = [c for c in cells if box[0] <= c["r"] <= box[2] and box[1] <= c["c"] <= box[3]]
        head = (f"\n== 「{item['name']}」 {item['rows']}행 × {item['cols']}열"
                + (f", 읽는 범위 {span(box)}" if box else "") + " ==")
        lines.append(head)
        if not cells:
            lines.append("(비어 있음)" if not box else "(이 범위는 비어 있습니다)")
            continue
        if item.get("colWidths") and not box:
            lines.append("열 너비(px): " + ", ".join(
                f"{get_column_letter(int(k) + 1)}={v}" for k, v in
                sorted(item["colWidths"].items(), key=lambda kv: int(kv[0]))[:20]))
        if item.get("merges") and not box:
            lines.append("병합: " + ", ".join(item["merges"][:20]))
        if formats := _formats(cells):
            lines.append("숫자 서식: " + "; ".join(formats))
        by_row: dict[int, list[dict[str, Any]]] = {}
        for cell in cells:
            by_row.setdefault(cell["r"], []).append(cell)
        used = sum(len(x) + 1 for x in lines)
        for index, r in enumerate(sorted(by_row)):
            line = " | ".join(_cell_text(c) for c in sorted(by_row[r], key=lambda c: c["c"]))
            if used + len(line) > budget:
                left = len(by_row) - index
                lines.append(f"… 이후 {left}개 행은 생략했습니다. 더 읽으려면 xlsx_read의 "
                             "sheet와 range(예: A41:F90)를 쓰세요.")
                break
            lines.append(line)
            used += len(line) + 1
    if model.get("truncated"):
        lines.append("(너무 커서 앞부분만 읽었습니다)")
    if model.get("kept"):
        lines.append("이 파일에는 여기서 읽거나 고칠 수 없는 것이 있고 그대로 보존됩니다: "
                     + ", ".join(model["kept"]))
    blanks_left = blanks(data)
    if blanks_left:
        lines.append("남은 빈칸: " + ", ".join(blanks_left))
    return "\n".join(lines)


_BLANK = re.compile(r"\[[^\[\]\n]{1,40}\]")


def blanks(data: bytes) -> list[str]:
    """The bracket blanks (「[담당자]」) still in the cells: `시트!B5 [담당자]`."""
    out: list[str] = []
    for item in sheet_model.to_model(data)["sheets"]:
        for cell in item["cells"]:
            value = cell.get("v")
            if isinstance(value, str) and "f" not in cell:
                for found in _BLANK.findall(value):
                    out.append(f"{item['name']}!{address(cell['r'], cell['c'])} {found}")
    return out[:30]


def plain_text(data: bytes) -> str:
    parts = []
    for item in sheet_model.to_model(data)["sheets"]:
        parts.extend(str(c["v"]) for c in item["cells"]
                     if isinstance(c.get("v"), str) and "f" not in c)
    return "\n".join(parts)


def contents(data: bytes) -> dict[str, int]:
    model = sheet_model.to_model(data)
    return {"sheets": len(model["sheets"]),
            "cells": sum(len(s["cells"]) for s in model["sheets"])}


def report(data: bytes) -> str:
    """The values the formulas give, as the saved file holds them (what a reply may quote)."""
    out: list[str] = []
    for item in sheet_model.to_model(data)["sheets"]:
        for cell in item["cells"]:
            if "f" in cell and len(out) < 40:
                shown = _shown(cell["v"]) if "v" in cell else "(계산하지 못함)"
                out.append(f"{item['name']}!{address(cell['r'], cell['c'])} ={cell['f']} → {shown}")
    if not out:
        return ""
    return "수식이 낸 값(저장된 파일 기준):\n" + "\n".join(out)
