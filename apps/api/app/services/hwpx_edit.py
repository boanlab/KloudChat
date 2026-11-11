"""Read and edit an `.hwpx` in place: a numbered outline for the model, and edit ops that keep
each paragraph's own shapes (`paraPrIDRef`, `charPrIDRef`).

Ids are positions in the current version: top-level paragraphs `p1…` and tables `t1…`, in
reading order across `Contents/section*.xml`. An insert or delete renumbers what follows, so
one `apply` call resolves every op against the version it was given, and the next call reads
again.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from app.services import office_edit
from app.services.office_edit import BLANK, Result, short

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_P, _RUN, _T, _TBL = f"{{{HP}}}p", f"{{{HP}}}run", f"{{{HP}}}t", f"{{{HP}}}tbl"
_TR, _TC, _SUBLIST = f"{{{HP}}}tr", f"{{{HP}}}tc", f"{{{HP}}}subList"
_LINESEG = f"{{{HP}}}linesegarray"
_LINEBREAK = f"{{{HP}}}lineBreak"

_SECTION = re.compile(r"Contents/section(\d{1,3})\.xml")

_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, remove_blank_text=False, huge_tree=False
)


class HwpxError(ValueError):
    """A file or an op this module refuses; the message is shown to the model."""


@dataclass
class _Table:
    id: str
    element: etree._Element
    #: the top-level paragraph holding it
    paragraph: str


@dataclass
class _Doc:
    names: list[str]
    roots: dict[str, etree._Element]
    #: top-level paragraphs in reading order
    paragraphs: list[etree._Element] = field(default_factory=list)
    tables: list[_Table] = field(default_factory=list)


def _sections(archive: zipfile.ZipFile) -> list[str]:
    found = [(int(m.group(1)), n) for n in archive.namelist() if (m := _SECTION.fullmatch(n))]
    return [name for _, name in sorted(found)]


def _load(data: bytes) -> tuple[zipfile.ZipFile, _Doc]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HwpxError("HWPX 파일이 아닙니다.") from exc
    names = _sections(archive)
    if not names:
        raise HwpxError("본문(section) 없는 HWPX입니다.")
    doc = _Doc(names=names, roots={})
    for name in names:
        try:
            root = etree.fromstring(archive.read(name), _PARSER)
        except etree.XMLSyntaxError as exc:
            raise HwpxError(f"{name}을 읽지 못했습니다.") from exc
        doc.roots[name] = root
        for paragraph in root:
            if paragraph.tag != _P:
                continue
            doc.paragraphs.append(paragraph)
            for table in paragraph.iter(_TBL):
                # nested tables live inside a cell; only the outer one gets an id
                if any(a.tag == _TBL for a in table.iterancestors()):
                    continue
                doc.tables.append(
                    _Table(f"t{len(doc.tables) + 1}", table, f"p{len(doc.paragraphs)}")
                )
    return archive, doc


def _own_text(paragraph: etree._Element) -> str:
    """Text of a paragraph's own runs, not of tables or text boxes inside it."""
    out: list[str] = []
    for run in paragraph.iterchildren(_RUN):
        for t in run.iterchildren(_T):
            out.append(t.text or "")
            for child in t:
                if child.tag == _LINEBREAK:
                    out.append("\n")
                out.append(child.tail or "")
    return "".join(out)


def _cells(table: etree._Element) -> list[tuple[int, int, etree._Element]]:
    out: list[tuple[int, int, etree._Element]] = []
    for row_index, row in enumerate(table.iterchildren(_TR)):
        for col_index, cell in enumerate(row.iterchildren(_TC)):
            addr = cell.find(f"{{{HP}}}cellAddr")
            try:
                r = int(addr.get("rowAddr")) if addr is not None else row_index
                c = int(addr.get("colAddr")) if addr is not None else col_index
            except (TypeError, ValueError):
                r, c = row_index, col_index
            out.append((r, c, cell))
    return out


def _cell_text(cell: etree._Element) -> str:
    sub = cell.find(_SUBLIST)
    paragraphs = sub.iterchildren(_P) if sub is not None else []
    return "\n".join(_own_text(p) for p in paragraphs).strip("\n")


def _shape(table: etree._Element) -> tuple[int, int]:
    cells = _cells(table)
    rows = max((r for r, _, _ in cells), default=-1) + 1
    cols = max((c for _, c, _ in cells), default=-1) + 1
    return rows, cols


def _merged(table: etree._Element) -> bool:
    for span in table.iter(f"{{{HP}}}cellSpan"):
        if span.get("rowSpan", "1") != "1" or span.get("colSpan", "1") != "1":
            return True
    return False


def blanks(data: bytes) -> list[str]:
    """Bracket blanks still in the file, first-seen order."""
    _, doc = _load(data)
    seen: dict[str, None] = {}
    for text in _texts(doc):
        for found in BLANK.finditer(text):
            seen.setdefault(found.group(0))
    return list(seen)


def _texts(doc: _Doc) -> list[str]:
    """Paragraph and cell texts in reading order."""
    out: list[str] = []
    for index, paragraph in enumerate(doc.paragraphs, start=1):
        out.append(_own_text(paragraph))
        for table in doc.tables:
            if table.paragraph == f"p{index}":
                out += [_cell_text(cell) for _, _, cell in _cells(table.element)]
    return out


def outline(data: bytes) -> str:
    """Numbered outline: `p3: text`, `t1 (3×2)` with `r0: a | b` rows, then the blanks."""
    _, doc = _load(data)
    tables_at: dict[str, list[_Table]] = {}
    for table in doc.tables:
        tables_at.setdefault(table.paragraph, []).append(table)
    out = office_edit.Outline()
    for index, paragraph in enumerate(doc.paragraphs, start=1):
        pid = f"p{index}"
        text = _own_text(paragraph)
        if not text.strip() and pid not in tables_at:
            out.empty(index)
            continue
        out.paragraph(index, text)
        for table in tables_at.get(pid, []):
            cells = [(r, c, _cell_text(cell)) for r, c, cell in _cells(table.element)]
            out.table(table.id, _shape(table.element), _merged(table.element), cells, pid)
    return out.render()


def plain_text(data: bytes) -> str:
    """Body text, one line per paragraph or table row (preview and search)."""
    _, doc = _load(data)
    return "\n".join(t for t in _texts(doc) if t.strip())


def restyle_first(data: bytes, *, para_pr: str, char_pr: str) -> bytes:
    """The first paragraph's shapes set (a blank page typed in body text, not title size)."""
    archive, doc = _load(data)
    first = doc.paragraphs[0]
    first.set("paraPrIDRef", para_pr)
    for run in first.iterchildren(_RUN):
        run.set("charPrIDRef", char_pr)
    return _write(archive, doc)


# ── edits ──


def _set_text(paragraph: etree._Element, text: str) -> None:
    """Paragraph text replaced in its first text run; that run's shape is kept, other text
    runs are dropped, and controls (section settings, tables) stay where they are."""
    runs = list(paragraph.iterchildren(_RUN))
    holder = next((t for run in runs for t in run.iterchildren(_T)), None)
    if holder is None:
        run = runs[0] if runs else etree.SubElement(paragraph, _RUN, charPrIDRef="0")
        holder = etree.SubElement(run, _T)
    for run in runs:
        for t in list(run.iterchildren(_T)):
            if t is not holder:
                run.remove(t)
        if run is not holder.getparent() and len(run) == 0:
            paragraph.remove(run)
    for child in list(holder):
        holder.remove(child)
    lines = text.split("\n")
    holder.text = lines[0]
    for line in lines[1:]:
        etree.SubElement(holder, _LINEBREAK).tail = line
    _relayout(paragraph)


def _relayout(paragraph: etree._Element) -> None:
    """Drop stored line layout so the viewer lays the new text out again."""
    for stale in list(paragraph.iterchildren(_LINESEG)):
        paragraph.remove(stale)


def _filled(text: str, old: str, new: str) -> tuple[str, int]:
    return office_edit.filled(text, old, new)


def _replace_in(paragraph: etree._Element, old: str, new: str) -> int:
    """Replace `old` inside single `<hp:t>` texts (shape kept); spanning runs: whole text."""
    count = 0
    for run in paragraph.iterchildren(_RUN):
        for t in run.iterchildren(_T):
            if t.text and old in t.text:
                t.text, n = _filled(t.text, old, new)
                count += n
            for child in t:
                if child.tail and old in child.tail:
                    child.tail, n = _filled(child.tail, old, new)
                    count += n
    if not count:
        text = _own_text(paragraph)
        if old in text:
            text, count = _filled(text, old, new)
            _set_text(paragraph, text)
    if count:
        _relayout(paragraph)
    return count


def _new_paragraph(like: etree._Element, text: str) -> etree._Element:
    """A paragraph with `like`'s paragraph and character shapes."""
    paragraph = etree.Element(_P, nsmap=like.nsmap)
    for name in ("id", "paraPrIDRef", "styleIDRef", "pageBreak", "columnBreak", "merged"):
        if like.get(name) is not None:
            paragraph.set(name, "0" if name == "pageBreak" else like.get(name))
    first = next(
        (run for run in like.iterchildren(_RUN) if run.find(_T) is not None),
        like.find(_RUN),
    )
    run = etree.SubElement(paragraph, _RUN, charPrIDRef=first.get("charPrIDRef", "0")
                           if first is not None else "0")
    etree.SubElement(run, _T)
    _set_text(paragraph, text)
    return paragraph


def _text_like(doc: _Doc, index: int) -> etree._Element:
    """Nearest text paragraph at or before `index`, for the shapes of a new one."""
    for at in range(index, -1, -1):
        candidate = doc.paragraphs[at]
        if candidate.find(f".//{_TBL}") is None and candidate.find(f".//{{{HP}}}secPr") is None:
            return candidate
    return doc.paragraphs[index]


def _paragraph(doc: _Doc, pid: object) -> tuple[int, etree._Element]:
    found = re.fullmatch(r"p(\d{1,5})", str(pid or "").strip())
    if not found or not 1 <= int(found.group(1)) <= len(doc.paragraphs):
        raise HwpxError(f"문단 {pid!r}이 없습니다 (p1~p{len(doc.paragraphs)}).")
    index = int(found.group(1)) - 1
    return index, doc.paragraphs[index]


def _table(doc: _Doc, tid: object) -> etree._Element:
    for table in doc.tables:
        if table.id == str(tid or "").strip():
            return table.element
    raise HwpxError(f"표 {tid!r}가 없습니다 (표 {len(doc.tables)}개).")


def _cell(table: etree._Element, row: object, col: object) -> etree._Element:
    try:
        r, c = int(row), int(col)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise HwpxError("row·col은 0부터 세는 숫자입니다.") from exc
    for at_r, at_c, cell in _cells(table):
        if (at_r, at_c) == (r, c):
            return cell
    rows, cols = _shape(table)
    raise HwpxError(f"r{r}c{c} 칸이 없습니다 (표 {rows}×{cols}, 0부터).")


def _cell_paragraphs(cell: etree._Element) -> list[etree._Element]:
    sub = cell.find(_SUBLIST)
    return list(sub.iterchildren(_P)) if sub is not None else []


def _everywhere(doc: _Doc) -> list[etree._Element]:
    """Every paragraph a blank can stand in: body, then the cells of each table."""
    out = list(doc.paragraphs)
    for table in doc.tables:
        for cell in table.element.iter(_TC):
            out += _cell_paragraphs(cell)
    return out


def _places(doc: _Doc, at: object) -> list[etree._Element]:
    """`p6` or `t1 r2c1`: the paragraphs one fill is kept to."""
    text = str(at or "").strip()
    if found := re.fullmatch(r"(t\d{1,4})\s{0,2}r(\d{1,4})c(\d{1,4})", text):
        table = _table(doc, found.group(1))
        return _cell_paragraphs(_cell(table, found.group(2), found.group(3)))
    return [_paragraph(doc, text)[1]]


def _set_cell(cell: etree._Element, text: str) -> None:
    sub = cell.find(_SUBLIST)
    paragraphs = list(sub.iterchildren(_P)) if sub is not None else []
    if not paragraphs:
        raise HwpxError("문단 없는 칸입니다.")
    for extra in paragraphs[1:]:
        sub.remove(extra)
    _set_text(paragraphs[0], text)


def _renumber_rows(table: etree._Element) -> None:
    rows = list(table.iterchildren(_TR))
    for r, row in enumerate(rows):
        for cell in row.iterchildren(_TC):
            addr = cell.find(f"{{{HP}}}cellAddr")
            if addr is not None:
                addr.set("rowAddr", str(r))
    table.set("rowCnt", str(len(rows)))
    size = table.find(f"{{{HP}}}sz")
    if size is not None:
        height = 0
        for row in rows:
            cell_size = next(iter(row.iter(f"{{{HP}}}cellSz")), None)
            height += int(cell_size.get("height", "0") or 0) if cell_size is not None else 0
        if height:
            size.set("height", str(height))


def _row(table: etree._Element, at: object) -> tuple[int, list[etree._Element]]:
    if _merged(table):
        raise HwpxError("병합 셀이 있는 표는 줄을 넣거나 뺄 수 없습니다.")
    rows = list(table.iterchildren(_TR))
    try:
        index = int(at)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise HwpxError("row는 0부터 세는 숫자입니다.") from exc
    if not 0 <= index < len(rows):
        raise HwpxError(f"r{index} 줄이 없습니다 (0~{len(rows) - 1}).")
    return index, rows


def _text_arg(op: dict, name: str = "text") -> str:
    return office_edit.text_arg(op, name, HwpxError)


def apply(data: bytes, ops: list[dict]) -> Result:
    """Every op or none: targets resolve against `data` before anything changes."""
    office_edit.check_ops(ops, HwpxError)
    archive, doc = _load(data)
    # resolve first: ids name the version the model read
    plan: list[tuple[dict, object]] = []
    for op in ops:
        if not isinstance(op, dict):
            raise HwpxError("op마다 {\"op\": ...} 객체여야 합니다.")
        kind = op.get("op")
        if kind == "replace_text":
            plan.append((op, _paragraph(doc, op.get("id"))[1]))
            _text_arg(op)
        elif kind == "fill_blank":
            blank = str(op.get("blank") or "").strip()
            if not BLANK.fullmatch(blank):
                raise HwpxError(f"fill_blank: {blank!r}은 「[이름]」 꼴 빈칸이 아닙니다.")
            _text_arg(op, "value")
            places = _places(doc, op.get("at")) if op.get("at") else _everywhere(doc)
            if not any(blank in _own_text(p) for p in places):
                where = f" {op.get('at')}에" if op.get("at") else ""
                raise HwpxError(f"fill_blank:{where} {blank}이 없습니다.")
            plan.append((op, places))
        elif kind == "set_cell":
            table = _table(doc, op.get("table"))
            plan.append((op, _cell(table, op.get("row"), op.get("col"))))
            _text_arg(op)
        elif kind == "insert_paragraph":
            _text_arg(op)
            plan.append((op, _paragraph(doc, op.get("after"))))
        elif kind == "delete_paragraph":
            paragraph = _paragraph(doc, op.get("id"))[1]
            if paragraph.find(f".//{{{HP}}}secPr") is not None:
                raise HwpxError(
                    f"{op.get('id')}에는 쪽 설정이 있어 지울 수 없습니다. 글만 비우세요."
                )
            plan.append((op, paragraph))
        elif kind in ("insert_table_row", "delete_table_row"):
            table = _table(doc, op.get("table"))
            index, rows = _row(table, op.get("after" if kind == "insert_table_row" else "row"))
            if kind == "delete_table_row" and len(rows) < 2:
                raise HwpxError("줄이 하나뿐인 표의 줄은 지울 수 없습니다.")
            plan.append((op, (table, rows[index])))
        else:
            raise office_edit.unknown_op(kind, HwpxError)

    changes: list[str] = []
    for op, target in plan:
        kind = op["op"]
        if kind == "replace_text":
            before = _own_text(target)
            _set_text(target, _text_arg(op))
            changes.append(f"{op['id']}: 「{short(before)}」 → 「{short(op['text'])}」")
        elif kind == "fill_blank":
            blank, value = op["blank"].strip(), _text_arg(op, "value")
            count = sum(_replace_in(p, blank, value) for p in target)
            where = f"{op['at']}: " if op.get("at") else ""
            changes.append(f"{where}{blank} → 「{short(value)}」 ({count}곳)")
        elif kind == "set_cell":
            _set_cell(target, _text_arg(op))
            changes.append(f"{op['table']} r{op['row']}c{op['col']} → 「{short(op['text'])}」")
        elif kind == "insert_paragraph":
            index, anchor = target
            like = _text_like(doc, index)
            anchor.addnext(_new_paragraph(like, _text_arg(op)))
            changes.append(f"{op['after']} 뒤에 문단 추가: 「{short(op['text'])}」")
        elif kind == "delete_paragraph":
            before = _own_text(target)
            target.getparent().remove(target)
            changes.append(f"{op['id']} 삭제: 「{short(before)}」")
        elif kind == "insert_table_row":
            table, row = target
            fresh = copy.deepcopy(row)
            for cell in fresh.iterchildren(_TC):
                _set_cell(cell, "")
                for span in cell.iter(f"{{{HP}}}cellSpan"):
                    span.set("rowSpan", "1")
                cell.set("header", "0")
            row.addnext(fresh)
            _renumber_rows(table)
            changes.append(f"{op['table']} r{op['after']} 아래에 줄 추가")
        elif kind == "delete_table_row":
            table, row = target
            table.remove(row)
            _renumber_rows(table)
            changes.append(f"{op['table']} r{op['row']} 줄 삭제")
    return Result(_write(archive, doc), changes)


def _write(archive: zipfile.ZipFile, doc: _Doc) -> bytes:
    """Same archive, edited sections swapped in; `mimetype` first and stored, as OWPML wants."""
    out = io.BytesIO()
    preview = "\n".join(t for t in _texts(doc) if t.strip())[:1000]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        names = archive.namelist()
        if "mimetype" in names:
            target.writestr(
                zipfile.ZipInfo("mimetype"), archive.read("mimetype"), zipfile.ZIP_STORED
            )
        for name in names:
            if name == "mimetype":
                continue
            if name in doc.roots:
                body = etree.tostring(
                    doc.roots[name], xml_declaration=True, encoding="UTF-8", standalone=True
                )
            elif name == "Preview/PrvText.txt":
                body = preview.encode("utf-8")
            else:
                body = archive.read(name)
            target.writestr(name, body)
    return out.getvalue()
