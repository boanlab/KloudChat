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
from app.services.files import open_capped_zip
from app.services.office_edit import BLANK, Result, short

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
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
    #: files added to the package (a picture's `BinData/…`), stored as they are
    added: dict[str, bytes] = field(default_factory=dict)


def _sections(archive: zipfile.ZipFile) -> list[str]:
    found = [(int(m.group(1)), n) for n in archive.namelist() if (m := _SECTION.fullmatch(n))]
    return [name for _, name in sorted(found)]


def _load(data: bytes) -> tuple[zipfile.ZipFile, _Doc]:
    try:
        archive = open_capped_zip(data)
    except zipfile.BadZipFile as exc:
        raise HwpxError("HWPX 파일이 아닙니다.") from exc
    except RuntimeError as exc:  # zip bomb limits
        raise HwpxError(str(exc)) from exc
    names = _sections(archive)
    if not names:
        raise HwpxError("본문(section) 없는 HWPX입니다.")
    doc = _Doc(names=names, roots={})
    for name in names:
        try:
            root = etree.fromstring(archive.read(name), _PARSER)
        except (etree.XMLSyntaxError, zipfile.BadZipFile) as exc:
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


def _char_sizes(archive: zipfile.ZipFile) -> dict[str, tuple[int, bool]]:
    """`charPr` id → (height, bold), from the header; empty when it cannot be read."""
    try:
        header = etree.fromstring(archive.read("Contents/header.xml"), _PARSER)
    except (KeyError, etree.XMLSyntaxError):
        return {}
    out: dict[str, tuple[int, bool]] = {}
    for shape in header.iter(f"{{{HH}}}charPr"):
        try:
            height = int(shape.get("height") or 0)
        except ValueError:
            continue
        out[str(shape.get("id"))] = (height, shape.find(f"{{{HH}}}bold") is not None)
    return out


def _heading_marks(archive: zipfile.ZipFile, doc: _Doc) -> dict[int, str]:
    """「 (title)」, 「 (heading 1)」…: short bold lines set larger than the body, by size, as
    Word's outline shows its heading styles (`docx_edit._mark`); 「3장」 is then countable."""
    sizes = _char_sizes(archive)
    shaped: list[tuple[int, int, bool, int]] = []  # (index, height, bold, length)
    for index, paragraph in enumerate(doc.paragraphs):
        run = next((r for r in paragraph.iterchildren(_RUN) if r.find(_T) is not None), None)
        text = _own_text(paragraph).strip()
        if run is None or not text or paragraph.find(f".//{_TBL}") is not None:
            continue
        height, bold = sizes.get(str(run.get("charPrIDRef")), (0, False))
        shaped.append((index, height, bold, len(text)))
    if not shaped:
        return {}
    heights = [height for _, height, _, _ in shaped]
    body = max(set(heights), key=heights.count)
    # a heading is set larger than the body (bold or not: a light style keeps its size)
    heads = [(index, height) for index, height, _, n in shaped if height > body and n <= 80]
    levels = sorted({height for _, height in heads}, reverse=True)
    if not levels:
        return {}
    marks: dict[int, str] = {}
    if len(levels) > 1 and sum(1 for _, h in heads if h == levels[0]) == 1:
        title = next(index for index, h in heads if h == levels[0])
        marks[title] = " (title)"
        # a cover's label above the title is no chapter
        heads = [(index, h) for index, h in heads if index > title]
        levels = sorted({h for _, h in heads}, reverse=True)
    names = {h: f"heading {level}" for level, h in enumerate(levels[:3], start=1)}
    marks.update({index: f" ({names[h]})" for index, h in heads if h in names})
    return marks


def outline(data: bytes) -> str:
    """Numbered outline: `p3: text`, `t1 (3×2)` with `r0: a | b` rows, then the blanks."""
    archive, doc = _load(data)
    tables_at: dict[str, list[_Table]] = {}
    for table in doc.tables:
        tables_at.setdefault(table.paragraph, []).append(table)
    marks = _heading_marks(archive, doc)
    out = office_edit.Outline()
    for index, paragraph in enumerate(doc.paragraphs, start=1):
        pid = f"p{index}"
        text = _own_text(paragraph)
        pictured = paragraph.find(f".//{{{HP}}}pic") is not None
        if not text.strip() and pid not in tables_at and not pictured:
            out.empty(index)
            continue
        if pictured and not text.strip():
            text = "(그림)"
        out.paragraph(index, text, marks.get(index - 1, ""))
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


#: a paragraph's text leaf: (holder, `text` or `tail` of it, or `break` for a line break)
_Piece = tuple[etree._Element, str, str]


def _pieces(paragraph: etree._Element) -> list[_Piece]:
    """The paragraph's own text in order, as `_own_text` reads it: each `<hp:t>`'s text, a
    line break `\\n`, and the text after each control inside the `<hp:t>`."""
    out: list[_Piece] = []
    for run in paragraph.iterchildren(_RUN):
        for t in run.iterchildren(_T):
            out.append((t, "text", t.text or ""))
            for child in t:
                if child.tag == _LINEBREAK:
                    out.append((child, "break", "\n"))
                out.append((child, "tail", child.tail or ""))
    return out


def _put(holder: etree._Element, where: str, value: str) -> None:
    if where == "text":
        holder.text = value
    else:
        holder.tail = value


def _starts(pieces: list[_Piece]) -> list[int]:
    starts, at = [], 0
    for _, _, text in pieces:
        starts.append(at)
        at += len(text)
    return starts


def _splice(pieces: list[_Piece], start: int, end: int, value: str) -> bool:
    """Characters `start:end` of the paragraph replaced by `value`, written into the text
    piece the first of them is in and cut from the pieces after it, so every run outside the
    span keeps its own shape. False (nothing changed) when the span crosses a line break."""
    starts = _starts(pieces)
    hit = [(piece, at) for piece, at in zip(pieces, starts, strict=True)
           if at < end and start < at + len(piece[2])]
    if any(where == "break" for (_, where, _), _ in hit):
        return False
    if start == end:
        # an insertion: into the text the character before it is in (the first at 0)
        holder = next(((piece, at) for piece, at in zip(pieces, starts, strict=True)
                       if piece[1] != "break" and at < start <= at + len(piece[2])), None)
        if holder is None and start == 0:
            holder = next(((piece, at) for piece, at in zip(pieces, starts, strict=True)
                           if piece[1] != "break"), None)
        if holder is None:
            return False
        (element, where, text), at = holder
        _put(element, where, text[: start - at] + value + text[start - at:])
        return True
    placed = False
    for (element, where, text), at in hit:
        head = text[: max(0, start - at)]
        tail = text[end - at:] if end - at < len(text) else ""
        _put(element, where, head + (value if not placed else "") + tail)
        placed = True
    return placed


def _rewrite(paragraph: etree._Element, text: str) -> None:
    """The paragraph's text made `text`, touching only what changed: the text both share at
    the start and at the end stays in its own runs (a bold 「수신」 label stays bold, a red
    word stays red), and the changed middle takes the shape of the run it begins in. A change
    with nowhere to sit (no shared start or end, or one across a line break) rewrites the
    paragraph in its first run (`_set_text`)."""
    pieces = _pieces(paragraph)
    old = "".join(t for _, _, t in pieces)
    if old == text:
        return
    pre = 0
    while pre < min(len(old), len(text)) and old[pre] == text[pre]:
        pre += 1
    suf = 0
    while (suf < min(len(old), len(text)) - pre
           and old[len(old) - 1 - suf] == text[len(text) - 1 - suf]):
        suf += 1
    value = text[pre:len(text) - suf]
    if (not pre and not suf) or "\n" in value or not _splice(
            pieces, pre, len(old) - suf, value):
        _set_text(paragraph, text)
        return
    _relayout(paragraph)


def _relayout(paragraph: etree._Element) -> None:
    """Drop stored line layout so the viewer lays the new text out again."""
    for stale in list(paragraph.iterchildren(_LINESEG)):
        paragraph.remove(stale)


def _filled(text: str, old: str, new: str) -> tuple[str, int]:
    return office_edit.filled(text, old, new)


def _replace_in(paragraph: etree._Element, old: str, new: str) -> int:
    """Every `old` in the paragraph, across runs, replaced by `new` and an agreeing particle;
    the shape of the run each one starts in is kept, the runs around it untouched."""
    pieces = _pieces(paragraph)
    text = "".join(t for _, _, t in pieces)
    found = list(office_edit.blank_with_particle(old).finditer(text))
    if not found:
        return 0
    if "\n" in new:
        _set_text(paragraph, office_edit.filled(text, old, new)[0])
        return len(found)
    # right to left: what is cut never moves a place still to fill
    for match in reversed(found):
        if not _splice(_pieces(paragraph), match.start(), match.end(),
                       office_edit.put(new, match.group(1))):
            _rewrite(paragraph, office_edit.filled(_own_text(paragraph), old, new)[0])
            break
    _relayout(paragraph)
    return len(found)


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
    run = etree.SubElement(
        paragraph, _RUN, charPrIDRef=first.get("charPrIDRef", "0") if first is not None else "0"
    )
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


def _holds_more(paragraph: etree._Element) -> bool:
    """The paragraph carries more than text: a nested table, a picture, a shape, a field."""
    return any(
        child.tag not in (_T, f"{{{HP}}}secPr", f"{{{HP}}}ctrl")
        for run in paragraph.iterchildren(_RUN) for child in run
    ) or paragraph.find(f".//{_TBL}") is not None


def _set_cell(cell: etree._Element, text: str, *, whole: bool = False) -> None:
    """The cell's text made `text`, one line per text paragraph in order: a line that stays
    keeps its runs, a changed one keeps the shapes around its change (`_rewrite`); lines past
    the cell's paragraphs become new paragraphs like its last, and text paragraphs past the
    lines go. A paragraph holding a nested table or a picture is never one of them and
    stays. `whole`: the cell emptied to one paragraph with `text` (nested tables too),
    asked for in so many words."""
    sub = cell.find(_SUBLIST)
    paragraphs = list(sub.iterchildren(_P)) if sub is not None else []
    if not paragraphs:
        raise HwpxError("문단 없는 칸입니다.")
    if whole:
        for extra in paragraphs[1:]:
            sub.remove(extra)
        for run in paragraphs[0].iterchildren(_RUN):
            for child in list(run):
                if child.tag != _T:
                    run.remove(child)
        _set_text(paragraphs[0], text)
        return
    lines = text.split("\n")
    texts = [p for p in paragraphs if not _holds_more(p)]
    if not texts:
        # only a nested table or picture: the text goes in a new paragraph before it
        fresh = _new_paragraph(_text_like_in(paragraphs), "")
        paragraphs[0].addprevious(fresh)
        texts = [fresh]
    for paragraph, line in zip(texts, lines, strict=False):
        _rewrite(paragraph, line)
    for paragraph in texts[len(lines):]:
        sub.remove(paragraph)
    last = texts[min(len(lines), len(texts)) - 1]
    for line in reversed(lines[len(texts):]):
        last.addnext(_new_paragraph(last, line))


def _text_like_in(paragraphs: list[etree._Element]) -> etree._Element:
    """A cell paragraph whose shapes a new one takes: the first holding text, else the first."""
    return next((p for p in paragraphs if p.find(f".//{_T}") is not None), paragraphs[0])


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


def apply(data: bytes, ops: list[dict],
          pictures: dict[str, tuple[str, bytes]] | None = None) -> Result:
    """Every op or none: targets resolve against `data` before anything changes. `pictures`:
    the image files picture ops name (`file`), found by the tool."""
    from app.services import hwpx_ops

    office_edit.check_ops(ops, HwpxError)
    archive, doc = _load(data)
    # resolve first: ids name the version the model read
    plan: list[tuple[dict, object]] = []
    #: house shapes put into this file's header, once (`_import_shapes`)
    shapes: dict[str, dict[str, str]] | None = None
    for op in ops:
        if not isinstance(op, dict):
            raise HwpxError('op마다 {"op": ...} 객체여야 합니다.')
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
        elif kind == "insert_table":
            rows = office_edit.table_arg(op, HwpxError)
            if shapes is None:
                shapes = _import_shapes(_header(archive, doc))
            plan.append((op, (_paragraph(doc, op.get("after"))[1],
                              _remapped(_table_paragraph(rows), shapes))))
        elif kind in hwpx_ops.OPS:
            plan.append((op, hwpx_ops.resolve(archive, doc, op, pictures or {})))
        else:
            raise office_edit.unknown_op(kind, HwpxError)

    changes: list[str] = []
    for op, target in plan:
        kind = op["op"]
        if kind == "replace_text":
            before = _own_text(target)
            _rewrite(target, _text_arg(op))
            changes.append(f"{op['id']}: 「{short(before)}」 → 「{short(op['text'])}」")
        elif kind == "fill_blank":
            blank, value = op["blank"].strip(), _text_arg(op, "value")
            count = sum(_replace_in(p, blank, value) for p in target)
            where = f"{op['at']}: " if op.get("at") else ""
            changes.append(f"{where}{blank} → 「{short(value)}」 ({count}곳)")
        elif kind == "set_cell":
            _set_cell(target, _text_arg(op), whole=op.get("whole") is True)
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
                _set_cell(cell, "", whole=True)
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
        elif kind == "insert_table":
            anchor, made = target
            anchor.addnext(made)
            rows = op["rows"]
            changes.append(f"{op['after']} 뒤에 표 추가 ({len(rows)}줄 × "
                           f"{max(len(row) for row in rows)}칸)")
        elif kind in hwpx_ops.OPS:
            changes.append(hwpx_ops.run(archive, doc, op, target))
    return Result(_write(archive, doc), changes)


#: the shapes a new table is drawn in, by `report_export`'s house ids: bordered cells, the
#: thick-ruled head row and the table's own frame; body and bold body text; the body and the
#: left-aligned cell paragraph
_HOUSE = {"borderFill": ("1", "2", "3"), "charPr": ("0", "1"), "paraPr": ("3", "7")}
#: where each kind's definitions sit in `header.xml`
_LISTS = {"borderFill": "borderFills", "charPr": "charProperties", "paraPr": "paraProperties"}
#: attributes that point at a definition, and its kind
_REFS = {"borderFillIDRef": "borderFill", "charPrIDRef": "charPr", "paraPrIDRef": "paraPr"}
_FONT_SLOTS = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")
_HEADER = "Contents/header.xml"


def _house_header() -> etree._Element:
    """`header.xml` of a file the HWPX export writes: where the house shapes are defined."""
    from app.services import report_export

    data = report_export.to_hwpx("표", [{"id": "b", "heading": "", "content": "표"}])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return etree.fromstring(archive.read(_HEADER), _PARSER)


def _bare(element: etree._Element) -> bytes:
    """A definition without its id, to tell an equal one already in the file."""
    copied = copy.deepcopy(element)
    copied.attrib.pop("id", None)
    for node in copied.iter():
        node.tail = None
        if node.text is not None and not node.text.strip():
            node.text = None
    return etree.tostring(copied, method="c14n", exclusive=True)


def _font_id(head: etree._Element, lang: str, face: str) -> str:
    """The id of font `face` in the file's `lang` list, added there when missing."""
    faces = next((f for f in head.iter(f"{{{HH}}}fontface") if f.get("lang") == lang), None)
    if faces is None:
        raise HwpxError("문서 머리에 글꼴 목록이 없어 표를 넣을 수 없습니다.")
    for font in faces.iterchildren(f"{{{HH}}}font"):
        if font.get("face") == face:
            return str(font.get("id"))
    ids = [int(f.get("id")) for f in faces.iterchildren(f"{{{HH}}}font")
           if str(f.get("id") or "").isdigit()]
    new = str(max(ids, default=-1) + 1)
    etree.SubElement(faces, f"{{{HH}}}font", id=new, face=face, type="TTF", isEmbedded="0")
    faces.set("fontCnt", str(len(faces.findall(f"{{{HH}}}font"))))
    return new


def _listing(head: etree._Element, kind: str) -> etree._Element:
    listing = next(head.iter(f"{{{HH}}}{_LISTS[kind]}"), None)
    if listing is None:
        raise HwpxError("문서 머리(header.xml)에 모양 목록이 없습니다.")
    return listing


def _define(head: etree._Element, kind: str, made: etree._Element) -> str:
    """The id of a definition equal to `made` in the file's header, `made` added under a fresh
    id when there is none."""
    listing = _listing(head, kind)
    defined = {str(e.get("id")): e for e in listing.iterchildren(f"{{{HH}}}{kind}")}
    same = next((i for i, e in defined.items() if _bare(e) == _bare(made)), None)
    if same is not None:
        return same
    ids = [int(i) for i in defined if i.isdigit()]
    same = str(max(ids, default=-1) + 1)
    made = copy.deepcopy(made)
    made.set("id", same)
    made.tail = listing[-1].tail if len(listing) else None
    listing.append(made)
    listing.set("itemCnt", str(len(defined) + 1))
    return same


def _import_shapes(head: etree._Element, wanted: dict[str, tuple[str, ...]] | None = None
                   ) -> dict[str, dict[str, str]]:
    """House shapes (`wanted`, the new table's by default) put into a file's own header under
    ids of its own: an equal definition already there is reused, any other is added with a
    fresh id, so a file made in 한글 (whose `charPr` 1 may be a red 20 pt title) never lends
    its own meaning to what is added. House id -> file id, per kind."""
    wanted = wanted or _HOUSE
    house = _house_header()
    house_faces = {
        (f.getparent().get("lang") or "").lower(): {}
        for f in house.iter(f"{{{HH}}}font")
    }
    for font in house.iter(f"{{{HH}}}font"):
        house_faces[(font.getparent().get("lang") or "").lower()][str(font.get("id"))] = \
            font.get("face")
    remap: dict[str, dict[str, str]] = {kind: {} for kind in _LISTS}
    for kind in ("borderFill", "charPr", "paraPr"):
        for house_id in wanted.get(kind, ()):
            source = next((e for e in house.iter(f"{{{HH}}}{kind}")
                           if e.get("id") == house_id), None)
            if source is None:
                raise HwpxError("문서 모양을 찾지 못했습니다.")
            made = copy.deepcopy(source)
            for node in made.iter():
                ref = node.get("borderFillIDRef")
                if ref is not None and ref in remap["borderFill"]:
                    node.set("borderFillIDRef", remap["borderFill"][ref])
            if kind == "charPr" and (fonts := made.find(f"{{{HH}}}fontRef")) is not None:
                for slot in _FONT_SLOTS:
                    face = house_faces.get(slot, {}).get(str(fonts.get(slot)))
                    if face:
                        fonts.set(slot, _font_id(head, slot.upper(), face))
            remap[kind][house_id] = _define(head, kind, made)
    return remap


def _remapped(element: etree._Element, remap: dict[str, dict[str, str]]) -> etree._Element:
    """`element` with every shape reference moved to the file's own ids."""
    for node in element.iter():
        for attr, kind in _REFS.items():
            value = node.get(attr)
            if value is not None and value in remap.get(kind, {}):
                node.set(attr, remap[kind][value])
    return element


def _header(archive: zipfile.ZipFile, doc: _Doc) -> etree._Element:
    """The file's `header.xml`, read once and written back with the sections."""
    if _HEADER not in doc.roots:
        try:
            doc.roots[_HEADER] = etree.fromstring(archive.read(_HEADER), _PARSER)
        except (KeyError, etree.XMLSyntaxError) as exc:
            raise HwpxError("문서 머리(header.xml)를 읽지 못해 표를 넣을 수 없습니다.") from exc
    return doc.roots[_HEADER]


def _table_paragraph(rows: list[list[str]]) -> etree._Element:
    """A table paragraph as the HWPX export draws one, head row first."""
    from app.services import report_export

    try:
        xml = report_export._hwpx_table(rows)
    except Exception as exc:  # noqa: BLE001 — the exporter's own refusals, said to the model
        raise HwpxError(f"insert_table: 표를 만들지 못했습니다 ({exc}).") from exc
    wrapped = etree.fromstring(
        f'<w xmlns:hp="{HP}" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">{xml}</w>',
        _PARSER,
    )
    return wrapped[0]


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
        for name in [*names, *(n for n in doc.added if n not in names)]:
            if name == "mimetype":
                continue
            if name in doc.added:
                target.writestr(zipfile.ZipInfo(name), doc.added[name], zipfile.ZIP_STORED)
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
