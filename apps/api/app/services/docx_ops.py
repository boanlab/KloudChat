"""Word edit ops past text: a paragraph's style and alignment, a span's character format,
moving paragraphs, table columns and merges, pictures, and page setup (`docx_edit.apply`
resolves and runs them like its own). Each keeps everything it is not asked to change: a span
is formatted by splitting its runs at the span's ends, a column is added as a copy of its
neighbour, a picture keeps its paragraph.
"""

from __future__ import annotations

import copy
import io
import re
from typing import Any

from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml import etree

from app.services import docx_edit as de
from app.services import office_edit

OPS = (
    "set_style", "format_text", "align", "move_paragraphs", "insert_table_column",
    "delete_table_column", "merge_cells", "split_cell", "insert_picture", "replace_picture",
    "page_setup",
)
#: ops after which paragraph or table numbers are not the ones read
RENUMBERS = frozenset({"move_paragraphs", "insert_picture"})

#: the model's style names -> Word's built-in style
STYLES = {"title": "Title", "heading 1": "Heading 1", "heading 2": "Heading 2",
          "heading 3": "Heading 3", "body": "Normal"}
#: a heading style made when a file has none: size and bold, Word's own look
_MADE = {"Title": (26, True), "Heading 1": (16, True), "Heading 2": (14, True),
         "Heading 3": (12, True)}
_ALIGN = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
          "right": WD_ALIGN_PARAGRAPH.RIGHT, "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
_COLOUR = re.compile(r"#?([0-9A-Fa-f]{6})")
#: a picture's widest, as a share of the text column
MAX_PICTURE_MM = 400


def _err(message: str) -> de.DocxError:
    return de.DocxError(message)


def _where(doc: de._Doc, at: object) -> list[etree._Element]:
    """`p5` or `t1 r2c1`: the paragraphs an op acts on."""
    text = str(at or "").strip()
    if re.fullmatch(r"t\d{1,4}\s{0,2}r\d{1,4}c\d{1,4}", text):
        return de._places(doc, text)
    return [de._paragraph(doc, text)[1]]


def _style(doc: de._Doc, name: str) -> str:
    """Word's style for the model's name, made like Word's own when the file lacks it."""
    word = STYLES.get(" ".join(str(name or "").lower().split()))
    if word is None:
        raise _err(f"set_style: style은 {', '.join(STYLES)} 중 하나입니다.")
    styles = doc.document.styles
    if word not in [s.name for s in styles]:
        made = styles.add_style(word, WD_STYLE_TYPE.PARAGRAPH)
        made.base_style = styles["Normal"]
        size, bold = _MADE[word]
        made.font.size, made.font.bold = Pt(size), bold
        level = re.search(r"\d", word)
        if level:
            # outline level, so a table of contents and the navigation pane find it
            props = made.element.get_or_add_pPr()
            outline = etree.SubElement(props, qn("w:outlineLvl"))
            outline.set(qn("w:val"), str(int(level.group(0)) - 1))
    return word


def _explode(paragraph: etree._Element) -> None:
    """Every text run split into one run per text piece, its format copied: a span's ends
    can then fall between runs."""
    for run in de._text_runs(paragraph):
        children = [c for c in run if c.tag != qn("w:rPr")]
        if len(children) < 2:
            continue
        props = run.find(qn("w:rPr"))
        anchor = run
        for child in children[1:]:
            made = etree.Element(qn("w:r"))
            if props is not None:
                made.append(copy.deepcopy(props))
            made.append(child)
            anchor.addnext(made)
            anchor = made


def _split_at(paragraph: etree._Element, at: int) -> None:
    """A text run cut in two at character `at` of the paragraph, both halves in its format."""
    count = 0
    for run in de._text_runs(paragraph):
        leaf = next((c for c in run if c.tag != qn("w:rPr")), None)
        if leaf is None:
            continue
        size = len(leaf.text or "") if leaf.tag == qn("w:t") else 1
        if count < at < count + size and leaf.tag == qn("w:t"):
            text = leaf.text or ""
            cut = at - count
            twin = copy.deepcopy(run)
            leaf.text = text[:cut]
            leaf.set(de.XML_SPACE, "preserve")
            twin_leaf = next(c for c in twin if c.tag == qn("w:t"))
            twin_leaf.text = text[cut:]
            twin_leaf.set(de.XML_SPACE, "preserve")
            run.addnext(twin)
            return
        count += size


def _span_runs(paragraph: etree._Element, find: str) -> list[etree._Element]:
    """The text runs that hold `find` (each place it stands), split so they hold only it;
    every run when `find` is empty."""
    _explode(paragraph)
    text = de._own_text(paragraph)
    if not find:
        return de._text_runs(paragraph)
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(find), text)]
    for start, end in spans:
        _split_at(paragraph, start)
        _split_at(paragraph, end)
    out: list[etree._Element] = []
    count = 0
    for run in de._text_runs(paragraph):
        leaf = next((c for c in run if c.tag != qn("w:rPr")), None)
        size = (len(leaf.text or "") if leaf is not None and leaf.tag == qn("w:t")
                else 1 if leaf is not None else 0)
        if size and any(start <= count and count + size <= end for start, end in spans):
            out.append(run)
        count += size
    return out


def _format(op: dict) -> dict[str, Any]:
    """What `format_text` sets: only the keys given."""
    out: dict[str, Any] = {}
    for key in ("bold", "italic", "underline"):
        if key in op:
            if not isinstance(op[key], bool):
                raise _err(f"format_text: {key}는 true나 false입니다.")
            out[key] = op[key]
    if op.get("color") not in (None, ""):
        found = _COLOUR.fullmatch(str(op["color"]).strip())
        if not found:
            raise _err("format_text: color는 '#C00000'처럼 16진 색입니다.")
        out["color"] = found.group(1).upper()
    if op.get("size") not in (None, ""):
        try:
            size = float(op["size"])
        except (TypeError, ValueError) as exc:
            raise _err("format_text: size는 pt 숫자입니다.") from exc
        if not 6 <= size <= 72:
            raise _err("format_text: size는 6~72pt입니다.")
        out["size"] = size
    if not out:
        raise _err("format_text: bold, italic, underline, color, size 중 하나는 주세요.")
    return out


def _put_format(run: etree._Element, paragraph: etree._Element, part: Any,
                wanted: dict[str, Any]) -> None:
    shown = Run(run, Paragraph(paragraph, part))
    for key in ("bold", "italic", "underline"):
        if key in wanted:
            setattr(shown.font, key, wanted[key])
    if "color" in wanted:
        shown.font.color.rgb = RGBColor.from_string(wanted["color"])
    if "size" in wanted:
        shown.font.size = Pt(wanted["size"])


def _block_range(doc: de._Doc, first: object, last: object) -> list[etree._Element]:
    """Body blocks from paragraph `first` to `last`, tables between them included."""
    i, start = de._paragraph(doc, first)
    j, end = de._paragraph(doc, last or first)
    if j < i:
        raise _err("move_paragraphs: to가 from보다 앞입니다.")
    a, b = doc.blocks.index(start), doc.blocks.index(end)
    blocks = doc.blocks[a:b + 1]
    if any(x.tag == de._P and x.find(f"{de._PPR}/{de._SECT}") is not None for x in blocks):
        raise _err("move_paragraphs: 구역 설정이 있는 문단은 옮길 수 없습니다.")
    return blocks


# ── tables ──


def _plain_table(table: etree._Element, what: str) -> list[etree._Element]:
    if de._merged(table):
        raise _err(f"{what}: 병합된 칸이 있는 표의 열은 바꿀 수 없습니다. "
                   "먼저 split_cell로 나누세요.")
    return list(table.iterchildren(de._TR))


def _col(op: dict, table: etree._Element, name: str) -> int:
    try:
        col = int(op.get(name))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise _err(f"{op.get('op')}: {name}은 0부터 세는 숫자입니다.") from exc
    cols = de._shape(table)[1]
    if not 0 <= col < cols:
        raise _err(f"{op.get('op')}: c{col} 열이 없습니다 (0~{cols - 1}).")
    return col


def _grid(table: etree._Element) -> etree._Element | None:
    return table.find(qn("w:tblGrid"))


def _width(cell: etree._Element) -> etree._Element | None:
    return cell.find(f"{qn('w:tcPr')}/{qn('w:tcW')}")


def _insert_column(table: etree._Element, after: int) -> None:
    grid = _grid(table)
    if grid is not None:
        cols = list(grid.iterchildren(qn("w:gridCol")))
        half = str(int(int(cols[after].get(qn("w:w"), "0") or 0) / 2))
        cols[after].set(qn("w:w"), half)
        made = copy.deepcopy(cols[after])
        cols[after].addnext(made)
    for row in table.iterchildren(de._TR):
        cell = list(row.iterchildren(de._TC))[after]
        fresh = copy.deepcopy(cell)
        de._set_cell(fresh, "", whole=True)
        for one in (cell, fresh):
            if (w := _width(one)) is not None and (w.get(qn("w:type")) in (None, "dxa")):
                w.set(qn("w:w"), str(int(int(w.get(qn("w:w"), "0") or 0) / 2)))
        cell.addnext(fresh)


def _delete_column(table: etree._Element, col: int) -> None:
    grid = _grid(table)
    if grid is not None:
        cols = list(grid.iterchildren(qn("w:gridCol")))
        gone = int(cols[col].get(qn("w:w"), "0") or 0)
        grid.remove(cols[col])
        rest = list(grid.iterchildren(qn("w:gridCol")))
        if rest:
            near = rest[min(col, len(rest) - 1)]
            near.set(qn("w:w"), str(int(near.get(qn("w:w"), "0") or 0) + gone))
    for row in table.iterchildren(de._TR):
        cells = list(row.iterchildren(de._TC))
        row.remove(cells[col])


def _merge(table: etree._Element, row: int, col: int, rows: int, cols: int) -> None:
    """Cells `row..row+rows-1` × `col..col+cols-1` made one, their text kept in the first."""
    grid_rows = list(table.iterchildren(de._TR))
    if row + rows > len(grid_rows) or col + cols > de._shape(table)[1]:
        raise _err("merge_cells: 표 밖까지 합칠 수 없습니다.")
    keep_text: list[etree._Element] = []
    anchor = None
    for r in range(row, row + rows):
        cells = de._row_cells(grid_rows[r])
        span = [cell for c, cell in cells if col <= c < col + cols]
        if any(de._span(cell) > 1 or cell.find(f"{qn('w:tcPr')}/{qn('w:vMerge')}") is not None
               for cell in span) or len(span) != cols:
            raise _err("merge_cells: 이미 병합된 칸과 겹칩니다. 먼저 split_cell로 나누세요.")
        first = span[0]
        for other in span[1:]:
            keep_text += [copy.deepcopy(p) for p in de._cell_paragraphs(other)
                          if de._own_text(p).strip()]
            grid_rows[r].remove(other)
        if cols > 1:
            props = first.find(qn("w:tcPr"))
            if props is None:
                props = etree.Element(qn("w:tcPr"))
                first.insert(0, props)
            for stale in props.findall(qn("w:gridSpan")):
                props.remove(stale)
            span_el = etree.Element(qn("w:gridSpan"))
            span_el.set(qn("w:val"), str(cols))
            # gridSpan follows tcW in tcPr
            width = props.find(qn("w:tcW"))
            if width is not None:
                width.addnext(span_el)
            else:
                props.insert(0, span_el)
        if rows > 1:
            props = first.find(qn("w:tcPr"))
            if props is None:
                props = etree.Element(qn("w:tcPr"))
                first.insert(0, props)
            merge = etree.Element(qn("w:vMerge"))
            if r == row:
                merge.set(qn("w:val"), "restart")
                anchor = first
            else:
                keep_text += [copy.deepcopy(p) for p in de._cell_paragraphs(first)
                              if de._own_text(p).strip()]
                for p in de._cell_paragraphs(first)[1:]:
                    first.remove(p)
                if de._cell_paragraphs(first):
                    de._set_text(de._cell_paragraphs(first)[0], "")
            after = props.find(qn("w:gridSpan"))
            if after is None:
                after = props.find(qn("w:tcW"))
            if after is not None:
                after.addnext(merge)
            else:
                props.insert(0, merge)
        elif r == row:
            anchor = first
    if anchor is not None and keep_text:
        last = de._cell_paragraphs(anchor)[-1]
        for paragraph in keep_text:
            last.addnext(paragraph)
            last = paragraph


def _split(table: etree._Element, row: int, col: int) -> None:
    grid_rows = list(table.iterchildren(de._TR))
    cell = next((c for r, c0, c in de._cells(table) if (r, c0) == (row, col)), None)
    if cell is None:
        raise _err(f"split_cell: r{row}c{col} 칸이 없습니다.")
    span = de._span(cell)
    props = cell.find(qn("w:tcPr"))
    merge = props.find(qn("w:vMerge")) if props is not None else None
    if span == 1 and merge is None:
        raise _err(f"split_cell: r{row}c{col}은 병합된 칸이 아닙니다.")
    if span > 1:
        for stale in props.findall(qn("w:gridSpan")):
            props.remove(stale)
        for _ in range(span - 1):
            fresh = copy.deepcopy(cell)
            de._set_cell(fresh, "", whole=True)
            fp = fresh.find(qn("w:tcPr"))
            for stale in fp.findall(qn("w:vMerge")) if fp is not None else []:
                fp.remove(stale)
            cell.addnext(fresh)
    if merge is not None:
        props.remove(merge)
        for r in range(row + 1, len(grid_rows)):
            below = next((c for c0, c in de._row_cells(grid_rows[r]) if c0 == col), None)
            bp = below.find(qn("w:tcPr")) if below is not None else None
            vm = bp.find(qn("w:vMerge")) if bp is not None else None
            if vm is None or vm.get(qn("w:val"), "continue") != "continue":
                break
            bp.remove(vm)
            if span > 1:
                for stale in bp.findall(qn("w:gridSpan")):
                    bp.remove(stale)
                for _ in range(span - 1):
                    fresh = copy.deepcopy(below)
                    de._set_cell(fresh, "", whole=True)
                    below.addnext(fresh)


# ── pictures ──


def _text_width_mm(doc: de._Doc) -> float:
    section = doc.document.sections[-1]
    try:
        return (section.page_width - section.left_margin - section.right_margin) / 36000
    except TypeError:
        return 160.0


def _picture_paragraph(doc: de._Doc, like: etree._Element, data: bytes,
                       width_mm: float | None) -> etree._Element:
    """A paragraph holding the picture, centred, made at the end of the body and handed back
    to be moved; as wide as asked, never wider than the text column."""
    from PIL import Image

    body = doc.document.element.body
    before = list(body)
    widest = min(_text_width_mm(doc), MAX_PICTURE_MM)
    try:
        with Image.open(io.BytesIO(data)) as picture:
            natural = picture.size[0] / 96 * 25.4
    except Exception as exc:  # noqa: BLE001 — not an image the editors draw
        raise _err("그림 파일을 읽지 못했습니다.") from exc
    width = min(width_mm or natural, widest)
    paragraph = doc.document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run().add_picture(io.BytesIO(data), width=Mm(width))
    made = [c for c in body if not any(c is b for b in before)]
    for element in made:
        body.remove(element)
    return made[0]


def _replace_picture(doc: de._Doc, paragraph: etree._Element, data: bytes) -> None:
    from PIL import Image

    blip = next(paragraph.iter(qn("a:blip")), None)
    extent = next(paragraph.iter(qn("wp:extent")), None)
    if blip is None or extent is None:
        raise _err("replace_picture: 그 문단에는 그림이 없습니다. insert_picture를 쓰세요.")
    rid, _ = doc.document.part.get_or_add_image(io.BytesIO(data))
    blip.set(qn("r:embed"), rid)
    try:
        with Image.open(io.BytesIO(data)) as picture:
            wide, high = picture.size
    except Exception as exc:  # noqa: BLE001
        raise _err("그림 파일을 읽지 못했습니다.") from exc
    cx = int(extent.get("cx") or 0)
    cy = int(cx * high / max(1, wide))
    extent.set("cy", str(cy))
    for ext in paragraph.iter(qn("a:ext")):
        if ext.get("cx") is not None:
            ext.set("cx", str(cx))
            ext.set("cy", str(cy))


# ── page setup ──


def _page(doc: de._Doc, op: dict) -> str:
    said = []
    sections = doc.document.sections
    orientation = op.get("orientation")
    if orientation not in (None, ""):
        if orientation not in ("portrait", "landscape"):
            raise _err("page_setup: orientation은 portrait 또는 landscape입니다.")
        for section in sections:
            wide = orientation == "landscape"
            if (section.page_width > section.page_height) != wide:
                section.page_width, section.page_height = section.page_height, section.page_width
            section.orientation = WD_ORIENT.LANDSCAPE if wide else WD_ORIENT.PORTRAIT
        said.append("가로" if orientation == "landscape" else "세로")
    margins = op.get("margins_mm")
    if isinstance(margins, dict) and margins:
        for side, value in margins.items():
            if side not in ("top", "bottom", "left", "right"):
                raise _err("page_setup: margins_mm은 top·bottom·left·right입니다.")
            try:
                mm = float(value)
            except (TypeError, ValueError) as exc:
                raise _err("page_setup: 여백은 mm 숫자입니다.") from exc
            if not 0 <= mm <= 60:
                raise _err("page_setup: 여백은 0~60mm입니다.")
            for section in sections:
                setattr(section, f"{side}_margin", Mm(mm))
        said.append("여백 " + ", ".join(f"{k} {v}mm" for k, v in margins.items()))
    for where in ("header", "footer"):
        if op.get(where) is not None:
            text = office_edit.text_arg(op, where, de.DocxError)
            for section in sections:
                story = section.header if where == "header" else section.footer
                story.is_linked_to_previous = False
                first = story.paragraphs[0]
                de._set_text(first._p, text)
            name = "머리글" if where == "header" else "바닥글"
            said.append(f"{name} 「{office_edit.short(text)}」")
    if op.get("page_numbers") is not None:
        if not isinstance(op["page_numbers"], bool):
            raise _err("page_setup: page_numbers는 true나 false입니다.")
        for section in sections:
            footer = section.footer
            footer.is_linked_to_previous = False
            for paragraph in footer.paragraphs:
                for field in list(paragraph._p.iter(qn("w:fldSimple"))):
                    if "PAGE" in (field.get(qn("w:instr")) or ""):
                        field.getparent().remove(field)
            if op["page_numbers"]:
                paragraph = footer.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                field = etree.SubElement(paragraph._p, qn("w:fldSimple"))
                field.set(qn("w:instr"), " PAGE ")
                run = etree.SubElement(field, qn("w:r"))
                etree.SubElement(run, qn("w:t")).text = "1"
        said.append("쪽 번호 " + ("넣음" if op["page_numbers"] else "뺌"))
    if not said:
        raise _err("page_setup: orientation, margins_mm, header, footer, page_numbers 중 하나는 "
                   "주세요.")
    return "쪽 설정: " + ", ".join(said)


# ── resolve and run ──


def resolve(doc: de._Doc, op: dict, pictures: dict[str, tuple[str, bytes]]) -> Any:
    """What `op` acts on, checked before anything changes."""
    kind = op.get("op")
    if kind == "set_style":
        return de._paragraph(doc, op.get("id"))[1], _style(doc, str(op.get("style") or ""))
    if kind == "format_text":
        places = _where(doc, op.get("id"))
        find = str(op.get("find") or "")
        if find and not any(find in de._own_text(p) for p in places):
            raise _err(f"format_text: {op.get('id')}에 「{find}」가 없습니다.")
        return places, find, _format(op)
    if kind == "align":
        if op.get("align") not in _ALIGN:
            raise _err(f"align: align은 {', '.join(_ALIGN)} 중 하나입니다.")
        return _where(doc, op.get("id"))
    if kind == "move_paragraphs":
        blocks = _block_range(doc, op.get("from"), op.get("to"))
        after = str(op.get("after") or "").strip()
        anchor = None if after == "p0" else de._paragraph(doc, after)[1]
        if anchor is not None and any(anchor is b for b in blocks):
            raise _err("move_paragraphs: 옮길 범위 안으로는 옮길 수 없습니다.")
        return blocks, anchor
    if kind in ("insert_table_column", "delete_table_column"):
        table = de._table(doc, op.get("table"))
        _plain_table(table, kind)
        col = _col(op, table, "after" if kind == "insert_table_column" else "col")
        if kind == "delete_table_column" and de._shape(table)[1] < 2:
            raise _err("delete_table_column: 열이 하나뿐인 표의 열은 지울 수 없습니다.")
        if kind == "insert_table_column" and de._shape(table)[1] >= office_edit.MAX_TABLE_COLS:
            raise _err(f"insert_table_column: 표는 {office_edit.MAX_TABLE_COLS}칸까지입니다.")
        return table, col
    if kind in ("merge_cells", "split_cell"):
        table = de._table(doc, op.get("table"))
        try:
            row, col = int(op.get("row")), int(op.get("col"))  # type: ignore[arg-type]
            rows, cols = int(op.get("span_rows") or 1), int(op.get("span_cols") or 1)
        except (TypeError, ValueError) as exc:
            raise _err(f"{kind}: row·col·span_rows·span_cols는 숫자입니다.") from exc
        if kind == "merge_cells" and rows * cols < 2:
            raise _err("merge_cells: span_rows나 span_cols로 둘 이상의 칸을 주세요.")
        return table, row, col, rows, cols
    if kind in ("insert_picture", "replace_picture"):
        name = str(op.get("file") or "").strip()
        if name not in pictures:
            raise _err(f"{kind}: 그림 파일 「{name}」을 찾지 못했습니다. 이 대화에 첨부했거나 "
                       "만든 PNG·JPEG 그림의 이름이나 id를 주세요.")
        if kind == "insert_picture":
            index, anchor = de._paragraph(doc, op.get("after"))
            return anchor, pictures[name][1], op.get("width_mm")
        return de._paragraph(doc, op.get("id"))[1], pictures[name][1]
    if kind == "page_setup":
        return None
    raise office_edit.unknown_op(kind, de.DocxError)


def run(doc: de._Doc, op: dict, target: Any) -> str:
    """Applies a resolved op; its line for the reply."""
    kind = op["op"]
    part = doc.document.part
    if kind == "set_style":
        paragraph, style = target
        Paragraph(paragraph, part).style = doc.document.styles[style]
        return f"{op['id']} 스타일 → {op['style']}"
    if kind == "format_text":
        places, find, wanted = target
        count = 0
        for paragraph in places:
            for run in _span_runs(paragraph, find):
                _put_format(run, paragraph, part, wanted)
                count += 1
        what = f"「{office_edit.short(find)}」" if find else "문단 전체"
        return f"{op['id']} {what} 글자 모양: " + ", ".join(f"{k}={v}" for k, v in wanted.items())
    if kind == "align":
        for paragraph in target:
            Paragraph(paragraph, part).alignment = _ALIGN[op["align"]]
        return f"{op['id']} 정렬 → {op['align']}"
    if kind == "move_paragraphs":
        blocks, anchor = target
        for block in blocks:
            block.getparent().remove(block)
        if anchor is None:
            body = doc.document.element.body
            first = body[0] if len(body) else None
            for block in blocks:
                if first is not None:
                    first.addprevious(block)
                else:
                    body.append(block)
        else:
            for block in reversed(blocks):
                anchor.addnext(block)
        span = f"{op['from']}~{op.get('to') or op['from']}"
        return f"{span}를 {'맨 앞' if anchor is None else op['after'] + ' 뒤'}로 옮김"
    if kind == "insert_table_column":
        table, col = target
        _insert_column(table, col)
        return f"{op['table']} c{col} 오른쪽에 열 추가"
    if kind == "delete_table_column":
        table, col = target
        _delete_column(table, col)
        return f"{op['table']} c{col} 열 삭제"
    if kind == "merge_cells":
        table, row, col, rows, cols = target
        _merge(table, row, col, rows, cols)
        return f"{op['table']} r{row}c{col}부터 {rows}×{cols} 칸 병합"
    if kind == "split_cell":
        table, row, col, _, _ = target
        _split(table, row, col)
        return f"{op['table']} r{row}c{col} 병합 풀기"
    if kind == "insert_picture":
        anchor, data, width = target
        try:
            width_mm = float(width) if width not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise _err("insert_picture: width_mm은 숫자입니다.") from exc
        anchor.addnext(_picture_paragraph(doc, anchor, data, width_mm))
        return f"{op['after']} 뒤에 그림 「{op['file']}」 넣음"
    if kind == "replace_picture":
        paragraph, data = target
        _replace_picture(doc, paragraph, data)
        return f"{op['id']} 그림을 「{op['file']}」로 바꿈"
    if kind == "page_setup":
        return _page(doc, op)
    raise office_edit.unknown_op(kind, de.DocxError)
