"""A new workbook that starts from the person's Excel form (양식): the form's sheets, styles,
number formats, widths, formulas, defined names and merged cells are the workbook as it is; only
what the form shows as an example is taken out.

The form's sample values are not facts: a number, date or true/false typed in a cell is cleared
(its style stays, so the cell is still an input cell), and so is guidance text (「※ 작성요령」) and
filler (「홍길동」, 「○○○」). Text that names a row or column (labels, headings) and every formula
stay. The cleared cells and the form's name ride in the file (`form_marks`), and `xlsx_read` tells
the model what the form holds and which cells it cleared, so it fills them through the same ops
(`sheet_edit`), from what the person gave, and leaves the rest as bracket blanks (「[매출액]」).
"""

from __future__ import annotations

import re
from typing import Any

from openpyxl.utils import column_index_from_string

from app.services import form_marks, form_profile, sheet_calc, sheet_model, sheet_patch
from app.services.sheet_edit import address

#: the cells named in the mark and the note, at most
MAX_LISTED = 60
#: the form's text that is guidance or filler, not a label
_SAMPLE = re.compile(form_profile._GUIDANCE.pattern + "|" + form_profile._FILL.pattern)  # noqa: SLF001

MARK = "xlsx"


_REF = re.compile(r"(?<![A-Za-z0-9_!:'])\$?([A-Z]{1,3})\$?(\d{1,7})(?![A-Za-z0-9_(:])")


def _fixed_numbers(model: dict[str, Any], names: dict[str, set[tuple[int, int]]]) -> set[tuple]:
    """The typed numbers that are the form's own structure, which stay like a label: a running
    number down a column (1, 2, 3 …), a year heading a row that names things, and a parameter
    (a rate, a unit price) that a formula or a defined name points at by itself with a label
    beside or above it. Every other typed number is an example."""
    out: set[tuple] = set()
    for sheet in model["sheets"]:
        name = sheet["name"]
        at = {(c["r"], c["c"]): c for c in sheet["cells"]}
        typed = {pos: c for pos, c in at.items() if _number(c)}
        pointed = set(names.get(name, ()))
        for cell in sheet["cells"]:
            if "f" in cell:
                for col, row in _REF.findall(cell["f"]):
                    pointed.add((int(row) - 1, column_index_from_string(col) - 1))
        columns: dict[int, list[int]] = {}
        for r, c in typed:
            columns.setdefault(c, []).append(r)
        for c, rows in columns.items():
            values = [typed[(r, c)]["v"] for r in sorted(rows)]
            if len(values) >= 3 and all(float(v).is_integer() for v in values) and all(
                    b - a == 1 for a, b in zip(values, values[1:], strict=False)):
                out.update((name, r, c) for r in rows)
        for (r, c), cell in typed.items():
            value = cell["v"]
            if float(value).is_integer() and 1900 <= value <= 2100 and not (
                    cell.get("s") or {}).get("numFmt") and any(
                        isinstance(x.get("v"), str) and "f" not in x
                        for (rr, _), x in at.items() if rr == r):
                out.add((name, r, c))
            label = at.get((r, c - 1), {})
            alone = sum(1 for (rr, _) in typed if rr == r) == 1
            if (r, c) in pointed and alone and isinstance(label.get("v"), str) and (
                    "f" not in label) and not _SAMPLE.search(label["v"]):
                out.add((name, r, c))
    return out


def _number(cell: dict[str, Any]) -> bool:
    return ("f" not in cell and isinstance(cell.get("v"), (int, float))
            and not isinstance(cell["v"], bool) and "date" not in str(
                (cell.get("s") or {}).get("numFmt", "")).lower() and "yy" not in str(
                (cell.get("s") or {}).get("numFmt", "")))


def _sample(cell: dict[str, Any], fixed: bool) -> bool:
    """A cell holding an example the form shows: a typed number, date or true/false that is not
    the form's own structure, or a guidance or filler text. A formula, a label and an empty cell
    are not."""
    if "f" in cell or "v" not in cell or fixed:
        return False
    value = cell["v"]
    if isinstance(value, str):
        return bool(_SAMPLE.search(value))
    return True


def _pointed_names(data: bytes) -> dict[str, set[tuple[int, int]]]:
    """Cells the workbook's defined names point at, by sheet."""
    import io

    import openpyxl

    out: dict[str, set[tuple[int, int]]] = {}
    try:
        book = openpyxl.load_workbook(io.BytesIO(data))
        for defined in book.defined_names.values():
            for sheet, ref in defined.destinations:
                if ":" not in ref:
                    cell = book[sheet][ref.replace("$", "")]
                    out.setdefault(sheet, set()).add((cell.row - 1, cell.column - 1))
    except Exception:  # noqa: BLE001 — names are a hint; the formulas still point
        return {}
    return out


def _runs(cells: list[tuple[int, int]]) -> list[str]:
    """`B5:B9`-style runs of cells: a column run for each stretch, a row run for the rest."""
    left = set(cells)
    out: list[str] = []
    for r, c in sorted(left):
        if (r, c) not in left:
            continue
        end = c
        while (r, end + 1) in left:
            end += 1
        for k in range(c, end + 1):
            left.discard((r, k))
        out.append(address(r, c) if end == c else f"{address(r, c)}:{address(r, end)}")
    return out


def start(form: Any) -> tuple[str, bytes]:
    """(title, workbook) for the form (`form_files.Resolved`): its copy, samples taken out and
    marked with what it follows."""
    data = form_profile.as_document(form.data)
    model = sheet_model.to_model(data)
    ops: list[dict[str, Any]] = []
    cleared: dict[str, list[tuple[int, int]]] = {}
    fixed = _fixed_numbers(model, _pointed_names(data))
    for sheet in model["sheets"]:
        for cell in sheet["cells"]:
            if _sample(cell, (sheet["name"], cell["r"], cell["c"]) in fixed):
                ops.append({"sheet": sheet["name"], "r": cell["r"], "c": cell["c"], "v": None})
                cleared.setdefault(sheet["name"], []).append((cell["r"], cell["c"]))
    if ops:
        data = sheet_patch.apply(data, ops)
    data = sheet_calc.refresh(data)
    listed = [f"{name}!{run}" for name, cells in cleared.items() for run in _runs(cells)]
    data = form_marks.mark(data, {
        "kind": MARK, "name": form.choice.name, "source": form.choice.source,
        "fileId": form.choice.file_id, "cleared": listed[:MAX_LISTED],
        "clearedCount": len(ops), "followed": {"cleared": len(ops)},
        "truncated": bool(model.get("truncated")),
    })
    title = re.sub(r"\.(?:xlsx|xltx)$", "", form.choice.name, flags=re.I)[:120] or "새 시트"
    return title, data


def mark_of(data: bytes) -> dict[str, Any] | None:
    """The mark of a workbook that started from a form, or None."""
    found = form_marks.read(data)
    return found if found and found.get("kind") == MARK else None


def note(data: bytes, model: dict[str, Any]) -> list[str]:
    """What `xlsx_read` tells the model first about a workbook that started from a form: the
    form's name, the rules for filling it, the cells whose examples were cleared and the
    formatted empty cells (the input cells) by sheet. `model` is `sheet_model.to_model(data)`."""
    mark = mark_of(data)
    if mark is None:
        return []
    lines = [
        f"이 시트는 사람의 엑셀 양식 「{mark.get('name', '')}」에서 시작했습니다. 양식의 시트·"
        "서식·표시 형식·너비·수식·병합은 그대로 두고 xlsx_edit set_cells로 칸만 채우세요: "
        "표를 새로 만들거나 시트를 갈아엎지 마세요.",
        "양식에 적혀 있던 예시 값(숫자·날짜·「※ 작성요령」·「홍길동」)은 사실이 아니라서 "
        "지웠습니다(순번·연도 머리글·수식이 가리키는 세율 같은 값은 양식의 구조라 그대로 "
        "둠). 사람이나 그의 자료가 준 값만 넣고, 모르는 값은 칸에 「[매출액]」처럼 "
        "빈칸 표시를 넣으세요. 수식 칸은 건드리지 말고 값만 채우세요.",
    ]
    if mark.get("cleared"):
        lines.append("예시를 지운 칸: " + ", ".join(mark["cleared"]) + (
            " …" if int(mark.get("clearedCount") or 0) > len(mark["cleared"]) else ""))
    for sheet in model["sheets"]:
        empty = [(c["r"], c["c"]) for c in sheet["cells"]
                 if "v" not in c and "f" not in c and (
                     (c.get("s") or {}).get("fill") or (c.get("s") or {}).get("border"))]
        if empty:
            runs = _runs(empty)
            lines.append(f"「{sheet['name']}」에서 서식만 있는 빈 칸(입력 칸): "
                         + ", ".join(runs[:30]) + (" …" if len(runs) > 30 else ""))
    return lines
