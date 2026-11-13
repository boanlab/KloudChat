"""Read and edit a `.docx` in place: a numbered outline for the model, and edit ops that keep
each paragraph's style and numbering, each run's character format and each cell's own
properties.

Ids are positions in the current version: body paragraphs `p1…` and body tables `t1…`, in
reading order. An insert or delete renumbers what follows, so one `apply` call resolves every
op against the version it was given, and the next call reads again. Word splits one line of
text across runs freely (spell check, revisions), so text is matched across runs and a
replacement takes the format of the run its first character was in.
"""

from __future__ import annotations

import copy
import io
import re
from dataclasses import dataclass, field

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.text.paragraph import Paragraph
from lxml import etree

from app.services import office_edit
from app.services.files import _open_zip
from app.services.office_edit import BLANK, Result, short

__all__ = ["BLANK", "DocxError", "Result", "apply", "blanks", "outline", "plain_text"]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _w(name: str) -> str:
    return f"{{{W}}}{name}"


_P, _R, _T, _TAB, _BR, _CR = _w("p"), _w("r"), _w("t"), _w("tab"), _w("br"), _w("cr")
_TBL, _TR, _TC, _PPR, _RPR = _w("tbl"), _w("tr"), _w("tc"), _w("pPr"), _w("rPr")
_SDT, _SDT_CONTENT, _SECT = _w("sdt"), _w("sdtContent"), _w("sectPr")
_TCPR, _TRPR, _VAL = _w("tcPr"), _w("trPr"), _w("val")
#: a run made of these alone is text; anything else (a field, a picture, a note mark) stays
_TEXTUAL = frozenset({
    _RPR, _T, _TAB, _BR, _CR, _w("lastRenderedPageBreak"), _w("softHyphen"),
    _w("noBreakHyphen"),
})
#: removed text and moved-away text are not the paragraph's text
_GONE = frozenset({_w("del"), _w("moveFrom")})
#: tracked-change records a copied format must not carry
_CHANGES = (_w("pPrChange"), _w("rPrChange"), _w("ins"), _w("del"))
_HEADING = re.compile(r"(?:heading|제목)\s{0,2}(\d)", re.I)
#: a written list marker before a tab, as report exports and 공문 write them: 「1.\t」, 「가.\t」
_MARKED = re.compile(r"(\s{0,8})(\S{1,6})\t")


class DocxError(ValueError):
    """A file or an op this module refuses; the message is shown to the model."""


@dataclass
class _Doc:
    document: object
    #: body paragraphs and tables in reading order
    blocks: list[etree._Element] = field(default_factory=list)
    paragraphs: list[etree._Element] = field(default_factory=list)
    tables: list[etree._Element] = field(default_factory=list)
    #: header and footer parts: (머리글|바닥글, root)
    stories: list[tuple[str, etree._Element]] = field(default_factory=list)


def _body_blocks(parent: etree._Element) -> list[etree._Element]:
    out: list[etree._Element] = []
    for child in parent.iterchildren():
        if child.tag in (_P, _TBL):
            out.append(child)
        elif child.tag == _SDT and (content := child.find(_SDT_CONTENT)) is not None:
            # a content control around paragraphs: they read as body paragraphs
            out += _body_blocks(content)
    return out


def _load(data: bytes) -> _Doc:
    if data[:4] != b"PK\x03\x04":
        raise DocxError("Word 문서(.docx)가 아닙니다.")
    try:
        # declared sizes first: a zip bomb is refused before anything unpacks it
        _open_zip(data).close()
        document = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — any unreadable package is the same answer
        raise DocxError("Word 문서(.docx)가 아니거나 손상된 파일입니다.") from exc
    doc = _Doc(document=document)
    doc.blocks = _body_blocks(document.element.body)
    doc.paragraphs = [b for b in doc.blocks if b.tag == _P]
    doc.tables = [b for b in doc.blocks if b.tag == _TBL]
    for rel in document.part.rels.values():
        if rel.is_external:
            continue
        if rel.reltype == RT.HEADER:
            doc.stories.append(("머리글", rel.target_part.element))
        elif rel.reltype == RT.FOOTER:
            doc.stories.append(("바닥글", rel.target_part.element))
    return doc


def _owned(element: etree._Element, paragraph: etree._Element) -> bool:
    """`element` is `paragraph`'s own, not a text box's inside it nor removed text."""
    for ancestor in element.iterancestors():
        if ancestor is paragraph:
            return True
        if ancestor.tag == _P or ancestor.tag in _GONE:
            return False
    return False


def _pieces(paragraph: etree._Element) -> list[tuple[etree._Element, str]]:
    """The paragraph's text leaves in order: `w:t` its text, a tab `\\t`, a line break
    `\\n` (a page break is layout, not text)."""
    out: list[tuple[etree._Element, str]] = []
    for leaf in paragraph.iter(_T, _TAB, _BR, _CR):
        if leaf.getparent().tag != _R or not _owned(leaf, paragraph):
            continue  # a tab stop in the paragraph's format, or someone else's text
        if leaf.tag == _T:
            out.append((leaf, leaf.text or ""))
        elif leaf.tag == _TAB:
            out.append((leaf, "\t"))
        elif leaf.get(_w("type"), "textWrapping") == "textWrapping":
            out.append((leaf, "\n"))
    return out


def _own_text(paragraph: etree._Element) -> str:
    return "".join(text for _, text in _pieces(paragraph))


def _text_runs(paragraph: etree._Element) -> list[etree._Element]:
    """Runs holding only text, in order."""
    runs = []
    for run in paragraph.iter(_R):
        if not _owned(run, paragraph) or any(c.tag not in _TEXTUAL for c in run):
            continue
        if any(c.tag == _BR and c.get(_w("type"), "textWrapping") != "textWrapping"
               for c in run):
            continue
        runs.append(run)
    return runs


def _clean_format(element: etree._Element) -> etree._Element:
    for change in list(element.iter(*_CHANGES)):
        change.getparent().remove(change)
    return element


def _fill_run(run: etree._Element, text: str) -> None:
    """The run's text set; tabs and line breaks as Word writes them; its format kept."""
    for child in list(run):
        if child.tag != _RPR:
            run.remove(child)
    for index, line in enumerate(text.split("\n")):
        if index:
            etree.SubElement(run, _BR)
        for part, piece in enumerate(line.split("\t")):
            if part:
                etree.SubElement(run, _TAB)
            if piece:
                leaf = etree.SubElement(run, _T)
                leaf.text = piece
                leaf.set(XML_SPACE, "preserve")


def _drop(run: etree._Element, paragraph: etree._Element) -> None:
    """A text run removed, and a hyperlink or revision left empty by it."""
    parent = run.getparent()
    parent.remove(run)
    while parent is not paragraph and parent.tag != _P and not len(parent):
        above = parent.getparent()
        above.remove(parent)
        parent = above


def _set_text(paragraph: etree._Element, text: str) -> None:
    """Paragraph text replaced in its first text run, whose format is kept; other text runs
    go, while fields, pictures and note marks stay where they are."""
    runs = _text_runs(paragraph)
    if runs:
        first = runs[0]
        for run in runs[1:]:
            _drop(run, paragraph)
    else:
        first = etree.Element(_R)
        mark = paragraph.find(f"{_PPR}/{_RPR}")
        if mark is not None:
            # the paragraph mark's format is what Word types in an empty paragraph
            first.append(_clean_format(copy.deepcopy(mark)))
        props = paragraph.find(_PPR)
        if props is not None:
            props.addnext(first)
        else:
            paragraph.insert(0, first)
    _fill_run(first, text)


def _keep_marker(before: str, text: str) -> str:
    """A written marker keeps its tab: 「1.\\t관련」 replaced by 「1. 관련: …」 stays 「1.\\t」."""
    found = _MARKED.match(before)
    if not found:
        return text
    indent, marker = found.groups()
    rest = text.lstrip(" ")
    if rest.startswith(marker + " "):
        return indent + marker + "\t" + rest[len(marker):].lstrip(" ")
    return text


def _rewrite(paragraph: etree._Element, text: str) -> None:
    """The paragraph's text made `text`, touching only what changed: the text both share at
    the start and at the end stays in its own runs (a bold 「수신」 label stays bold), and the
    changed middle takes the format of the run it begins in. A change with nowhere to sit
    (or with its own tabs or line breaks) rewrites the paragraph (`_set_text`)."""
    pieces = _pieces(paragraph)
    old = "".join(t for _, t in pieces)
    if old == text:
        return
    pre = 0
    while pre < min(len(old), len(text)) and old[pre] == text[pre]:
        pre += 1
    suf = 0
    while (suf < min(len(old), len(text)) - pre
           and old[len(old) - 1 - suf] == text[len(text) - 1 - suf]):
        suf += 1
    start, end, value = pre, len(old) - suf, text[pre:len(text) - suf]
    starts, at = [], 0
    for _, piece in pieces:
        starts.append(at)
        at += len(piece)
    if start == end:
        # a pure insertion: into the text the character before it is in
        holder = next((
            (leaf, at) for (leaf, piece), at in zip(pieces, starts, strict=True)
            if leaf.tag == _T and at < start <= at + len(piece)
        ), None)
    else:
        holder = next((
            (leaf, at) for (leaf, piece), at in zip(pieces, starts, strict=True)
            if leaf.tag == _T and at < end and start < at + len(piece)
        ), None)
    if (not pre and not suf) or holder is None or "\n" in value or "\t" in value:
        _set_text(paragraph, text)
        return
    if start == end:
        leaf, at = holder
        body = leaf.text or ""
        leaf.text = body[: start - at] + value + body[start - at:]
        leaf.set(XML_SPACE, "preserve")
        return
    _splice(pieces, starts, start, end, value)


def _splice(pieces: list[tuple[etree._Element, str]], starts: list[int],
            start: int, end: int, value: str) -> None:
    """Characters `start:end` of the paragraph replaced by `value`, written into the piece
    the first of them is in; the rest of the span is cut from the pieces after it."""
    placed = False
    for (leaf, original), at in zip(pieces, starts, strict=True):
        size = len(original)
        if at + size <= start or at >= end:
            continue
        if leaf.tag != _T:
            leaf.getparent().remove(leaf)
            continue
        text = leaf.text or ""
        head = text[: max(0, start - at)]
        # past `end` the text may already hold a later fill: cut by place, not by length
        tail = text[end - at:] if end - at < size else ""
        leaf.text = head + (value if not placed else "") + tail
        leaf.set(XML_SPACE, "preserve")
        placed = True


def _replace_in(paragraph: etree._Element, old: str, new: str) -> int:
    """Every `old` in the paragraph, across runs, replaced by `new` and an agreeing
    particle; the format of the run each one starts in is kept."""
    pieces = _pieces(paragraph)
    text = "".join(t for _, t in pieces)
    found = list(office_edit.blank_with_particle(old).finditer(text))
    if not found:
        return 0
    if "\n" in new or "\t" in new:
        _set_text(paragraph, office_edit.filled(text, old, new)[0])
        return len(found)
    starts, at = [], 0
    for _, piece in pieces:
        starts.append(at)
        at += len(piece)
    # right to left: what is cut never moves a place still to fill
    for match in reversed(found):
        _splice(pieces, starts, match.start(), match.end(), office_edit.put(new, match.group(1)))
    return len(found)


# ── tables ──


def _span(cell: etree._Element) -> int:
    span = cell.find(f"{_TCPR}/{_w('gridSpan')}")
    try:
        return max(1, int(span.get(_VAL, "1"))) if span is not None else 1
    except ValueError:
        return 1


def _continued(cell: etree._Element) -> bool:
    """The cell is the lower part of a vertical merge."""
    merge = cell.find(f"{_TCPR}/{_w('vMerge')}")
    return merge is not None and merge.get(_VAL, "continue") == "continue"


def _row_cells(row: etree._Element) -> list[tuple[int, etree._Element]]:
    """A row's cells at their grid columns, the lower parts of vertical merges included."""
    before = row.find(f"{_TRPR}/{_w('gridBefore')}")
    try:
        c = int(before.get(_VAL, "0")) if before is not None else 0
    except ValueError:
        c = 0
    out = []
    for cell in row.iterchildren(_TC):
        out.append((c, cell))
        c += _span(cell)
    return out


def _cells(table: etree._Element) -> list[tuple[int, int, etree._Element]]:
    """Each cell at its row and grid column; a merge is one cell, where it starts."""
    return [
        (r, c, cell)
        for r, row in enumerate(table.iterchildren(_TR))
        for c, cell in _row_cells(row)
        if not _continued(cell)
    ]


def _shape(table: etree._Element) -> tuple[int, int]:
    rows = list(table.iterchildren(_TR))
    cols = max((c + _span(cell) for _, c, cell in _cells(table)), default=0)
    return len(rows), cols


def _merged(table: etree._Element) -> bool:
    return any(
        _span(cell) > 1 or cell.find(f"{_TCPR}/{_w('vMerge')}") is not None
        for cell in table.iter(_TC)
    )


def _cell_paragraphs(cell: etree._Element) -> list[etree._Element]:
    return [b for b in _body_blocks(cell) if b.tag == _P]


def _cell_text(cell: etree._Element) -> str:
    return "\n".join(_own_text(p) for p in _cell_paragraphs(cell)).strip("\n")


def _set_cell(cell: etree._Element, text: str) -> None:
    """The cell holds one paragraph, the first, with `text`; its cell properties stay."""
    paragraphs = _cell_paragraphs(cell)
    if not paragraphs:
        raise DocxError("문단 없는 칸입니다.")
    for block in _body_blocks(cell):
        if block is not paragraphs[0]:
            block.getparent().remove(block)
    _set_text(paragraphs[0], text)


# ── reading ──


def _mark(doc: _Doc, paragraph: etree._Element) -> str:
    """「 (heading 1)」, 「 (title)」, 「 (list)」: what Word itself shows of the paragraph."""
    marks = []
    try:
        name = Paragraph(paragraph, doc.document.part).style.name or ""
    except (KeyError, ValueError, AttributeError):
        name = ""
    if name.lower() == "title":
        marks.append("title")
    elif found := _HEADING.match(name):
        marks.append(f"heading {found.group(1)}")
    if paragraph.find(f"{_PPR}/{_w('numPr')}") is not None or name.startswith("List"):
        marks.append("list")
    return f" ({', '.join(marks)})" if marks else ""


def _shown(table: etree._Element) -> list[tuple[int, int, str]]:
    """A merged table's cells named by grid column, 「c2~3: 세부 내용」, and the lower part
    of a vertical merge said, so a row's columns read where they stand."""
    out: list[tuple[int, int, str]] = []
    for r, row in enumerate(table.iterchildren(_TR)):
        for c, cell in _row_cells(row):
            span = _span(cell)
            name = f"c{c}" + (f"~{c + span - 1}" if span > 1 else "")
            text = "(위 칸과 병합)" if _continued(cell) else _cell_text(cell)
            out.append((r, c, f"{name}: {text}"))
    return out


def _story_paragraphs(doc: _Doc) -> list[tuple[str, etree._Element]]:
    return [(name, p) for name, root in doc.stories for p in root.iter(_P)]


def outline(data: bytes) -> str:
    """Numbered outline: `p3: text`, `t1 (표 3×2)` with `r0: a | b` rows, the headers and
    footers, then the blanks."""
    doc = _load(data)
    out = office_edit.Outline()
    index = 0
    for block in doc.blocks:
        if block.tag == _P:
            index += 1
            text = _own_text(block)
            if not text.strip():
                out.empty(index)
            else:
                out.paragraph(index, text, _mark(doc, block))
            continue
        tid = f"t{doc.tables.index(block) + 1}"
        merged = _merged(block)
        out.table(tid, _shape(block), merged, _shown(block) if merged else [
            (r, c, _cell_text(cell)) for r, c, cell in _cells(block)
        ])
    seen: set[str] = set()
    for name, root in doc.stories:
        text = " / ".join(t for p in root.iter(_P) if (t := _own_text(p).strip()))
        if text and (name, text) not in seen:
            seen.add((name, text))
            out.line(f"{name}: {text}")
            out.note(text, name)
    return out.render()


def _texts(doc: _Doc) -> list[str]:
    out: list[str] = []
    for block in doc.blocks:
        if block.tag == _P:
            out.append(_own_text(block))
        else:
            out += [_cell_text(cell) for _, _, cell in _cells(block)]
    return out


def blanks(data: bytes) -> list[str]:
    """Bracket blanks still in the file, first-seen order (headers and footers last)."""
    doc = _load(data)
    seen: dict[str, None] = {}
    texts = _texts(doc) + [_own_text(p) for _, p in _story_paragraphs(doc)]
    for text in texts:
        for found in BLANK.finditer(text):
            seen.setdefault(found.group(0))
    return list(seen)


def plain_text(data: bytes) -> str:
    """Body text, one line per paragraph or table cell (preview and search)."""
    return "\n".join(t for t in _texts(_load(data)) if t.strip())


# ── edits ──


def _paragraph(doc: _Doc, pid: object) -> tuple[int, etree._Element]:
    found = re.fullmatch(r"p(\d{1,5})", str(pid or "").strip())
    if not found or not 1 <= int(found.group(1)) <= len(doc.paragraphs):
        raise DocxError(f"문단 {pid!r}이 없습니다 (p1~p{len(doc.paragraphs)}).")
    index = int(found.group(1)) - 1
    return index, doc.paragraphs[index]


def _table(doc: _Doc, tid: object) -> etree._Element:
    found = re.fullmatch(r"t(\d{1,4})", str(tid or "").strip())
    if found and 1 <= int(found.group(1)) <= len(doc.tables):
        return doc.tables[int(found.group(1)) - 1]
    raise DocxError(f"표 {tid!r}가 없습니다 (표 {len(doc.tables)}개).")


def _cell(table: etree._Element, row: object, col: object) -> etree._Element:
    try:
        r, c = int(row), int(col)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DocxError("row·col은 0부터 세는 숫자입니다.") from exc
    for at_r, at_c, cell in _cells(table):
        if (at_r, at_c) == (r, c):
            return cell
    rows, cols = _shape(table)
    raise DocxError(f"r{r}c{c} 칸이 없습니다 (표 {rows}×{cols}, 0부터; 병합된 칸은 시작 칸으로).")


def _everywhere(doc: _Doc) -> list[etree._Element]:
    """Every paragraph a blank can stand in: body, cells, headers and footers."""
    body = list(doc.document.element.body.iter(_P))
    return body + [p for _, p in _story_paragraphs(doc)]


def _places(doc: _Doc, at: object) -> list[etree._Element]:
    """`p6` or `t1 r2c1`: the paragraphs one fill is kept to."""
    text = str(at or "").strip()
    if found := re.fullmatch(r"(t\d{1,4})\s{0,2}r(\d{1,4})c(\d{1,4})", text):
        table = _table(doc, found.group(1))
        return list(_cell(table, found.group(2), found.group(3)).iter(_P))
    return [_paragraph(doc, text)[1]]


def _text_like(doc: _Doc, index: int) -> etree._Element:
    """Nearest text paragraph at or before `index`, for the format of a new one."""
    for at in range(index, -1, -1):
        candidate = doc.paragraphs[at]
        if (candidate.find(f".//{_w('drawing')}") is None
                and candidate.find(f"{_PPR}/{_SECT}") is None):
            return candidate
    return doc.paragraphs[index]


def _new_paragraph(like: etree._Element, text: str) -> etree._Element:
    """A paragraph with `like`'s style, numbering and first run's format."""
    paragraph = etree.Element(_P)
    props = like.find(_PPR)
    if props is not None:
        props = _clean_format(copy.deepcopy(props))
        for name in (_SECT, _w("pageBreakBefore")):
            for stale in props.findall(name):
                props.remove(stale)
        paragraph.append(props)
    runs = _text_runs(like)
    run = etree.SubElement(paragraph, _R)
    source = runs[0].find(_RPR) if runs else None
    if source is not None:
        run.append(_clean_format(copy.deepcopy(source)))
    _fill_run(run, text)
    return paragraph


def _vertical(table: etree._Element) -> bool:
    return table.find(f".//{_TCPR}/{_w('vMerge')}") is not None


def _row(table: etree._Element, at: object) -> tuple[int, list[etree._Element]]:
    if _vertical(table):
        raise DocxError("세로로 병합된 칸이 있는 표는 줄을 넣거나 뺄 수 없습니다.")
    rows = list(table.iterchildren(_TR))
    try:
        index = int(at)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DocxError("row는 0부터 세는 숫자입니다.") from exc
    if not 0 <= index < len(rows):
        raise DocxError(f"r{index} 줄이 없습니다 (0~{len(rows) - 1}).")
    return index, rows


def _text_arg(op: dict, name: str = "text") -> str:
    return office_edit.text_arg(op, name, DocxError)


def apply(data: bytes, ops: list[dict]) -> Result:
    """Every op or none: targets resolve against `data` before anything changes."""
    office_edit.check_ops(ops, DocxError)
    doc = _load(data)
    plan: list[tuple[dict, object]] = []
    for op in ops:
        if not isinstance(op, dict):
            raise DocxError("op마다 {\"op\": ...} 객체여야 합니다.")
        kind = op.get("op")
        if kind == "replace_text":
            plan.append((op, _paragraph(doc, op.get("id"))[1]))
            _text_arg(op)
        elif kind == "fill_blank":
            blank = str(op.get("blank") or "").strip()
            if not BLANK.fullmatch(blank):
                raise DocxError(f"fill_blank: {blank!r}은 「[이름]」 꼴 빈칸이 아닙니다.")
            _text_arg(op, "value")
            places = _places(doc, op.get("at")) if op.get("at") else _everywhere(doc)
            if not any(blank in _own_text(p) for p in places):
                where = f" {op.get('at')}에" if op.get("at") else ""
                raise DocxError(f"fill_blank:{where} {blank}이 없습니다.")
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
            if paragraph.find(f"{_PPR}/{_SECT}") is not None:
                raise DocxError(
                    f"{op.get('id')}에는 구역 설정이 있어 지울 수 없습니다. 글만 비우세요."
                )
            if len(doc.paragraphs) < 2:
                raise DocxError("문서의 마지막 문단은 지울 수 없습니다. 글만 비우세요.")
            plan.append((op, paragraph))
        elif kind in ("insert_table_row", "delete_table_row"):
            table = _table(doc, op.get("table"))
            index, rows = _row(table, op.get("after" if kind == "insert_table_row" else "row"))
            if kind == "delete_table_row" and len(rows) < 2:
                raise DocxError("줄이 하나뿐인 표의 줄은 지울 수 없습니다.")
            plan.append((op, (table, rows[index])))
        elif kind == "insert_table":
            rows = office_edit.table_arg(op, DocxError)
            plan.append((op, (_paragraph(doc, op.get("after"))[1], rows)))
        else:
            raise office_edit.unknown_op(kind, DocxError)

    changes: list[str] = []
    for op, target in plan:
        kind = op["op"]
        if kind == "replace_text":
            before = _own_text(target)
            _rewrite(target, _keep_marker(before, _text_arg(op)))
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
            anchor.addnext(_new_paragraph(_text_like(doc, index), _text_arg(op)))
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
            # a copied heading row would repeat on every page; a revision mark is not new
            for name in ("tblHeader", "ins", "del", "trPrChange"):
                for stale in fresh.findall(f"{_TRPR}/{_w(name)}"):
                    stale.getparent().remove(stale)
            row.addnext(fresh)
            changes.append(f"{op['table']} r{op['after']} 아래에 줄 추가")
        elif kind == "delete_table_row":
            table, row = target
            table.remove(row)
            changes.append(f"{op['table']} r{op['row']} 줄 삭제")
        elif kind == "insert_table":
            anchor, rows = target
            for made in reversed(_new_table(doc, rows)):
                anchor.addnext(made)
            changes.append(f"{op['after']} 뒤에 표 추가 ({len(rows)}줄 × {len(rows[0])}칸)")
    out = io.BytesIO()
    doc.document.save(out)
    return Result(out.getvalue(), changes)


def _new_table(doc: _Doc, rows: list[list[str]]) -> list[etree._Element]:
    """A table as the Word export draws one (head row repeated, width shares), made at the end
    of the body and handed back to be moved; plain bordered cells in a file whose styles lack
    the table style."""
    from app.services import report_export

    body = doc.document.element.body
    # kept so lxml hands back the same proxies: `is` tells the new children apart
    before = list(body)
    try:
        report_export._docx_table(doc.document, rows)
    except Exception:  # noqa: BLE001 — a style the file lacks: drop the half-made table
        for child in [c for c in body if not any(c is b for b in before)]:
            body.remove(child)
        table = doc.document.add_table(rows=len(rows), cols=len(rows[0]))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                cell = table.cell(r, c)
                cell.text = text
                if r == 0:
                    for run in cell.paragraphs[0].runs:
                        run.bold = True
    made = [c for c in body if not any(c is b for b in before)]
    for element in made:
        body.remove(element)
    return made


def restyle_first(data: bytes, style: str) -> bytes:
    """The first paragraph in `style` (a blank page typed in body text, not title size)."""
    doc = _load(data)
    Paragraph(doc.paragraphs[0], doc.document.part).style = style
    out = io.BytesIO()
    doc.document.save(out)
    return out.getvalue()
