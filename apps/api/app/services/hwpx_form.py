"""A document written on a 한글 (HWPX) form (양식): the form itself becomes the conversation's HWP
document, and the writer fills it in place with the HWP tools (`hwpx_edit`: `set_cell` for a
value cell, `fill_fields` for several labelled values at once, `replace_text`/`insert_paragraph`
under a heading, `fill_blank` for a 「[ ]」), so the form's tables, cells, shapes and page stay
exactly as they are.

What the form says itself (a label, a heading, 「고려대학교 총장 귀하」) is fixed: the form's own
lines are kept in its mark (`start`), and `Guard` holds every edit to them, by code and not by
the writer's care. A write into a cell keeps the cell's fixed lines and adds the value after
them; a write that would replace a fixed paragraph, delete it, or fill a hairline spacer row
is left out and said (`Guard.notes`); after the edits every fixed line is checked again and
a cell that lost one is put back. The form's value cells, read when it was uploaded
(`form_profile`), are what the writer is told to fill (`rule`); after each save the document
says how many of them it filled (`progress`, read by `hwp_doc.describe`).
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from app.services import form_marks, hwpx_edit
from app.services.form_profile import _FILL, _GUIDANCE
from app.services.office_edit import BLANK

#: value cells listed for the writer at most
_LISTED = 40
#: a fixed line is at most this long (compact); longer ones are body or sample text
_FIXED_MAX = 40
#: a line of a cell of several fixed lines is fixed up to this long
_STRUCTURE_MAX = 140
#: a row shorter than this (hwpunit, 1/7200 in) holds nothing: a rule or a spacer
_SPACER = 1000
#: the fixed lines kept in the mark: cells and body paragraphs
_MAX_CELLS, _MAX_PARAS = 600, 400
_BULLET_ONLY = re.compile(r"^[\s\-–—·○□●■▪▷▶◦]*$")


def _compact(text: str) -> str:
    """A line as compared: no spaces, a 「[ ]」 blank whatever stands in it."""
    return re.sub(r"\s+", "", BLANK.sub("[]", text or ""))


def _inserted(fixed: str, line: str) -> bool:
    """`line` is the fixed line with something put in (every character of it, in order, and
    not much more than a value's worth added)."""
    if len(fixed) < 3 or len(line) > len(fixed) + 80 or len(line) <= len(fixed) \
            or not line.startswith(fixed[:3]):
        return False
    it = iter(line)
    return all(ch in it for ch in fixed)


def free_line(text: str) -> bool:
    """Room for a value, or guidance to replace: empty, a bullet, a blank, a 작성요령 line."""
    t = (text or "").strip()
    return (not t or bool(_BULLET_ONLY.fullmatch(t)) or bool(_FILL.fullmatch(t))
            or bool(_GUIDANCE.search(t)))


def fixed_line(text: str) -> bool:
    """A line the form says itself: short and neither free nor a bracket blank alone."""
    t = (text or "").strip()
    return (not free_line(t) and len(re.sub(r"\s+", "", t)) <= _FIXED_MAX
            and not BLANK.fullmatch(t))


def _lines(cell: etree._Element) -> list[str]:
    return hwpx_edit._cell_text(cell).split("\n")


def _height(cell: etree._Element) -> int:
    size = cell.find(f"{{{hwpx_edit.HP}}}cellSz")
    try:
        return int(size.get("height")) if size is not None else 99999
    except (TypeError, ValueError):
        return 99999


def spacer(cell: etree._Element) -> bool:
    """An empty cell in a row no taller than a rule: a spacer, never a place to write."""
    if hwpx_edit._cell_text(cell).strip():
        return False
    row = cell.getparent()
    cells = list(row.iterchildren(hwpx_edit._TC)) if row is not None else [cell]
    return all(_height(other) < _SPACER for other in cells)


def _span(cell: etree._Element) -> tuple[int, int]:
    span = cell.find(f"{{{hwpx_edit.HP}}}cellSpan")
    try:
        return (int(span.get("rowSpan") or 1), int(span.get("colSpan") or 1)) if span is not None \
            else (1, 1)
    except (TypeError, ValueError):
        return 1, 1


def _key(table: str, row: int, col: int) -> str:
    return f"{table}:{row}:{col}"


def snapshot(doc: Any) -> dict[str, Any]:
    """The fixed lines of a form: `cells` (`t1:4:1` → the cell's fixed lines), `paras` (the
    short fixed body paragraphs) and `rooms` (cells with a fixed line and room for a value)."""
    cells: dict[str, list[str]] = {}
    rooms: list[str] = []
    for table in doc.tables:
        for r, c, cell in hwpx_edit._cells(table.element):
            lines = _lines(cell)
            fixed = [ln for ln in lines if fixed_line(ln)]
            if len(fixed) >= 2:
                # a cell of several fixed lines is a structure (a receipt, a list of headings):
                # its longer lines are the form's too
                fixed = [ln for ln in lines if not free_line(ln) and not BLANK.fullmatch(
                    ln.strip()) and len(ln.strip()) <= _STRUCTURE_MAX]
            if not fixed or len(cells) >= _MAX_CELLS:
                continue
            cells[_key(table.id, r, c)] = fixed
            # room to write: a free line (a bullet, a blank) beside the fixed one, or a tall
            # cell holding only a label; a line across the whole table is a title
            full = _span(cell)[1] >= hwpx_edit._shape(table.element)[1]
            roomy = any(free_line(ln) for ln in lines) or (
                len(fixed) == 1 and _height(cell) >= 4000 and not full)
            if roomy:
                rooms.append(_key(table.id, r, c))
    paras = [t for t in (hwpx_edit._own_text(p).strip() for p in doc.paragraphs)
             if fixed_line(t)][:_MAX_PARAS]
    return {"cells": cells, "paras": paras, "rooms": rooms[:_MAX_CELLS]}


def start(form: Any) -> bytes:
    """The form (`form_files.Resolved`) as a new HWP document, marked with what it follows."""
    fields = [
        {k: f[k] for k in ("table", "row", "col", "label")}
        for f in (form.profile.get("fields") or [])[:120]
    ]
    mark: dict[str, Any] = {
        "kind": "hwpx", "name": form.choice.name, "source": form.choice.source,
        "fileId": form.choice.file_id, "fields": fields,
    }
    try:
        _, doc = hwpx_edit._load(form.data)
        mark["fixed"] = snapshot(doc)
        # a spacer row is not a value cell, whatever label sits above it
        tables = {t.id: t for t in doc.tables}
        kept = []
        for f in fields:
            table = tables.get(f["table"])
            cell = next((cl for r, c, cl in hwpx_edit._cells(table.element)
                         if (r, c) == (f["row"], f["col"])), None) if table else None
            if cell is None or not spacer(cell):
                kept.append(f)
        mark["fields"] = kept
    except hwpx_edit.HwpxError:
        pass
    return form_marks.mark(form.data, mark)


class Skip(Exception):  # noqa: N818 — an op left out, not an error
    """An op the form's fixed lines do not allow; its message is said to the writer."""


class Guard:
    """Holds the edits of one `hwpx_edit.apply` to the form's fixed lines."""

    def __init__(self, mark: dict[str, Any], doc: Any) -> None:
        fixed = mark.get("fixed") or {}
        self.mark = mark
        self.doc = doc
        self.cells: dict[str, list[str]] = fixed.get("cells") or {}
        self.paras = {_compact(t): t for t in fixed.get("paras") or []}
        self.fields = mark.get("fields") or []
        #: what was left out or changed, one sentence each
        self.notes: list[str] = []

    # ── where ──────────────────────────────────────────────────────────────

    def _tables(self) -> dict[str, Any]:
        return {t.id: t for t in self.doc.tables}

    def _cell_at(self, table: str, row: int, col: int) -> etree._Element | None:
        found = self._tables().get(table)
        if found is None:
            return None
        for r, c, cell in hwpx_edit._cells(found.element):
            if (r, c) == (row, col):
                return cell
        return None

    # ── fill_fields: labelled values, resolved here ─────────────────────────

    def expand(self, ops: list[dict]) -> list[dict]:
        """`fill_fields {values:[{label, text, table?}]}` as one `set_cell` per value."""
        out: list[dict] = []
        for op in ops:
            if not isinstance(op, dict) or op.get("op") != "fill_fields":
                out.append(op)
                continue
            values = op.get("values")
            if not isinstance(values, list) or not values:
                raise hwpx_edit.HwpxError(
                    'fill_fields: values가 필요합니다: {"op":"fill_fields","values":'
                    '[{"label":"①상호","text":"…"}]}.')
            for item in values:
                if not isinstance(item, dict) or not str(item.get("label") or "").strip() \
                        or not isinstance(item.get("text"), str):
                    raise hwpx_edit.HwpxError(
                        "fill_fields: 값마다 {label, text}가 있어야 합니다.")
                try:
                    out.append(self._resolve(item))
                except Skip as why:
                    self.notes.append(str(why))
        return out

    def _resolve(self, item: dict) -> dict:
        wanted = _compact(str(item["label"]))
        only = str(item.get("table") or "").strip()
        hits: list[tuple[str, int, int]] = []
        for key, lines in self.cells.items():
            table, r, c = key.split(":")
            if only and table != only:
                continue
            if any(wanted and wanted in _compact(ln) for ln in lines):
                hits.append((table, int(r), int(c)))
        if not hits:
            raise Skip(f"「{item['label']}」: 서식에서 그 이름의 칸을 찾지 못해 넣지 않았습니다.")
        if len(hits) > 1:
            where = ", ".join(f"{t} r{r}c{c}" for t, r, c in hits[:4])
            raise Skip(f"「{item['label']}」: 이름이 같은 칸이 여럿({where})이라 넣지 않았습니다. "
                       "table을 함께 주거나 set_cell로 칸을 가리키세요.")
        table, r, c = hits[0]
        target = self._value_cell(table, r, c)
        return {"op": "set_cell", "table": table, "row": target[0], "col": target[1],
                "text": item["text"]}

    def _value_cell(self, table: str, r: int, c: int) -> tuple[int, int]:
        """The cell a label's value goes in: the empty cell to its right, else below it,
        else the label's own cell (the value goes after the label)."""
        cell = self._cell_at(table, r, c)
        rows, cols = _span(cell) if cell is not None else (1, 1)
        for at in ((r, c + cols), (r + rows, c)):
            other = self._cell_at(table, *at)
            if other is None or _key(table, *at) in self.cells:
                continue
            if not hwpx_edit._cell_text(other).strip() or any(
                    f.get("table") == table and (f.get("row"), f.get("col")) == at
                    for f in self.fields):
                if not spacer(other):
                    return at
        return r, c

    # ── set_cell ───────────────────────────────────────────────────────────

    def cell_text(self, table: str, row: int, col: int, text: str) -> str:
        """The text a `set_cell` writes: the cell's fixed lines kept, `text` after them. A
        `Skip` for a spacer row, or a cell of several fixed lines `text` does not carry."""
        cell = self._cell_at(table, row, col)
        if cell is None:
            return text
        key = _key(table, row, col)
        if spacer(cell):
            raise Skip(f"{key.replace(':', ' r', 1).replace(':', 'c', 1)}: 구분선 줄이라 "
                       "넣지 않았습니다.")
        fixed = self.cells.get(key)
        if not fixed:
            return text
        mine = text.split("\n")
        have = [_compact(ln) for ln in mine]
        want = [_compact(ln) for ln in fixed]
        # every fixed line in order: the writer wrote the cell whole. A fixed line as it was,
        # or with a value put into its blank (「대학(Name) :」 → 「대학(Name) : 정보대학」); any
        # other line is content
        at, kept = 0, []
        for compact, line in zip(have, mine, strict=False):
            if at < len(want) and compact == want[at]:
                kept.append(fixed[at])
                at += 1
            elif at < len(want) and _inserted(want[at], compact):
                kept.append(line)
                at += 1
            else:
                kept.append(line)
        if at == len(want):
            return "\n".join(kept)
        if len(fixed) > 1:
            raise Skip(
                f"{table} r{row}c{col}: 이 칸은 「{' / '.join(f[:14] for f in fixed)}」 줄이 있어 "
                "그 줄을 그대로 두고 줄마다 아래에 내용을 쓴 칸 전체를 보내야 합니다. "
                "넣지 않았습니다.")
        extra = [ln for ln in mine if _compact(ln) != want[0]]
        self.notes.append(f"{table} r{row}c{col}: 칸의 「{fixed[0][:14]}」 줄은 두고 그 아래에 "
                          "넣었습니다.")
        return "\n".join([fixed[0], *extra])

    # ── replace_text / delete_paragraph ────────────────────────────────────

    def paragraph_text(self, pid: str, paragraph: etree._Element, text: str) -> str:
        """The text a `replace_text` writes: a fixed paragraph only takes insertions (a date
        line's numbers), its leading spaces kept; else a `Skip`."""
        old = hwpx_edit._own_text(paragraph).strip()
        if _compact(old) not in self.paras:
            return text
        it = iter(re.sub(r"\s+", "", text))
        if not all(ch in it for ch in re.sub(r"\s+", "", old)):
            raise Skip(f"{pid}: 「{old[:20]}」는 서식이 정한 글이라 바꾸지 않았습니다. 내용은 "
                       "그 아래 문단이나 빈칸에 쓰세요.")
        lead = re.match(r"\s*", hwpx_edit._own_text(paragraph)).group(0)
        return lead + text.lstrip() if lead and not text[:1].isspace() else text

    def may_delete(self, pid: str, paragraph: etree._Element) -> None:
        old = hwpx_edit._own_text(paragraph).strip()
        if _compact(old) in self.paras:
            raise Skip(f"{pid}: 「{old[:20]}」는 서식이 정한 글이라 지우지 않았습니다.")

    # ── after the edits ────────────────────────────────────────────────────

    def verify(self) -> None:
        """Every fixed line still there, in order, in its cell; a cell that lost one is put
        back to its fixed lines and said."""
        tables = self._tables()
        for key, fixed in self.cells.items():
            table, r, c = key.split(":")
            found = tables.get(table)
            if found is None:
                continue
            cell = next((cl for rr, cc, cl in hwpx_edit._cells(found.element)
                         if (rr, cc) == (int(r), int(c))), None)
            if cell is None:
                continue
            have = [_compact(ln) for ln in _lines(cell)]
            want = [_compact(ln) for ln in fixed]
            at = 0
            for line in have:
                if at < len(want) and (line == want[at] or _inserted(want[at], line)):
                    at += 1
            if at < len(want):
                hwpx_edit._set_cell(cell, "\n".join(fixed), whole=True)
                self.notes.append(f"{table} r{r}c{c}: 서식의 글이 바뀌어 원래대로 되돌렸습니다.")


def guard_for(data: bytes, doc: Any) -> Guard | None:
    """The guard of a document that follows a 한글 form, or None."""
    mark = form_marks.read(data)
    if not mark or mark.get("kind") != "hwpx" or not mark.get("fixed"):
        return None
    return Guard(mark, doc)


def progress(data: bytes, mark: dict[str, Any]) -> dict[str, Any]:
    """How many of the form's slots hold a value now, counted from the file: a value cell next
    to its label that is no longer empty, and a cell with a fixed line and room that holds
    something besides its fixed lines. The labels still empty are listed."""
    fields = mark.get("fields") or []
    fixed = mark.get("fixed") or {}
    rooms = [k for k in fixed.get("rooms") or [] if k in (fixed.get("cells") or {})]
    slots = [(_key(f["table"], f["row"], f["col"]), str(f.get("label") or "")) for f in fields]
    seen = {k for k, _ in slots}
    slots += [(k, (fixed["cells"][k] or [""])[0][:30]) for k in rooms if k not in seen]
    if not slots:
        return {"cells": 0, "cellsFilled": 0, "cellsEmpty": []}
    try:
        _, doc = hwpx_edit._load(data)
    except hwpx_edit.HwpxError:
        return {"cells": len(slots), "cellsFilled": 0, "cellsEmpty": []}
    tables = {t.id: t for t in doc.tables}
    cells: dict[str, etree._Element] = {}
    for table in tables.values():
        for r, c, cell in hwpx_edit._cells(table.element):
            cells[_key(table.id, r, c)] = cell
    filled, empty = 0, []
    for key, label in slots:
        cell = cells.get(key)
        lines = _lines(cell) if cell is not None else []
        own = [_compact(x) for x in (fixed.get("cells") or {}).get(key, [])]
        value = [ln for ln in lines if ln.strip() and _compact(ln) not in own
                 and not _FILL.fullmatch(ln.strip()) and not BLANK.fullmatch(ln.strip())
                 and not _BULLET_ONLY.fullmatch(ln.strip())]
        if value:
            filled += 1
        else:
            empty.append(label)
    return {"cells": len(slots), "cellsFilled": filled, "cellsEmpty": empty[:20]}


def rule(mark: dict[str, Any]) -> str:
    """What the writer is told about the 한글 form open beside the chat."""
    fields = mark.get("fields") or []
    fixed = mark.get("fixed") or {}
    lines = [
        f"The open HWP document is the person's own form 「{mark.get('name', '')}」. Fill it in "
        "place; never replace it with hwp_write. Read it once with hwp_read, then send ONE "
        "hwp_edit with every value (you have few tool calls; do not spend them on calculation "
        "or code tools):",
        "- fill_fields {values:[{label:'①상호', text:'…'}, …]}: each value by the label printed "
        "on the form (any distinctive part of it; add table when a label repeats); the "
        "server finds the cell to write in. Use set_cell {table,row,col,text} only for a "
        "cell fill_fields could not find;",
        "- the form's own lines (labels, headings, 「고려대학교 총장 귀하」, 「가. 사업의 방법」) "
        "stay as they are: a value goes after the label or under the heading, never over it; a "
        "write that would replace one is left out and the result says so. A cell that holds "
        "headings (「가. …」「나. …」) is written whole, the headings kept, each followed by "
        "its content;",
        "- a date line with blanks (「년 월 일」) takes replace_text with the numbers put in; "
        "text under a heading and any guidance (「※ 작성요령」, sample lines): replace that "
        "guidance with the content (replace_text, insert_paragraph after the heading);",
        "- a 「[ ]」 blank or checkbox: fill_blank. Spacer rows (a hairline) and the page "
        "footer line are never written.",
        "Take facts only from the person's request and material, never from the form's own "
        "sample text (「홍길동」, 「2025.00.00」). A value the material does not give stays a "
        "bracket blank such as 「[담당자]」; say which ones are left.",
        "Say plainly what the document holds when you are done. If you could not fill the form "
        "(the edits were refused or the cells could not be found), say so and ask the person "
        "for what is missing or to save the form again as HWPX; never tell the person to type "
        "it in themselves, and never say it is filled when the result says it is not.",
    ]
    if fields:
        listed = ", ".join(f"{f['label']}({f['table']} r{f['row']} c{f['col']})"
                           for f in fields[:_LISTED])
        lines.append(f"Value cells of this form: {listed}")
    if not fields and not (fixed.get("rooms") or []):
        lines.append(
            "This form's value cells could not be told apart reliably. Do not guess cell "
            "coordinates: tell the person so, plainly, and ask them to name the fields to "
            "fill or to save the form again in 한글 as HWPX; fill only what is clear.")
    return "\n".join(lines)


#: a reply sending the person to type the values in themselves, or saying the fill failed
_MANUAL = re.compile(r"직접\s{0,2}(?:입력|작성|채워|기입)|편집기에서\s{0,3}(?:직접|입력)|"
                     r"채우지\s{0,2}못|작성하지\s{0,2}못|넣지\s{0,2}못|도구\s{0,3}한도")


def nothing_saved(reply: str, refused: str) -> str:
    """What a form turn that saved nothing says, in place of a reply telling the person to type
    the values in: the form is as it was, why, and what would help. Empty when the reply is no
    such failure (a question about the form answered, say)."""
    if not refused and not _MANUAL.search(reply or ""):
        return ""
    why = f" 이유: {refused.strip().splitlines()[0][:200]}" if refused.strip() else ""
    return (
        "이번에는 양식에 아무것도 채우지 못했고, 양식은 처음 그대로입니다." + why + "\n\n"
        "채울 칸이나 값을 다시 알려 주시거나, 한글에서 이 양식을 「다른 이름으로 저장 › 한글 "
        "표준 문서 (*.hwpx)」로 다시 저장해 올려 주세요. 같은 요청을 다시 보내도 됩니다.")
