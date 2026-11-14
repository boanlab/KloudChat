"""HWP edit ops past text: a paragraph's style and alignment, a span's character format,
moving paragraphs, table columns and merges, pictures, and page setup (`hwpx_edit.apply`
resolves and runs them like its own). A shape (글자 모양, 문단 모양) is never changed in place,
since other paragraphs share its id: the changed copy is defined in the file's header under
an id of its own (`hwpx_edit._define`), or an equal one already there is reused.
"""

from __future__ import annotations

import copy
import io
import re
from typing import Any

from lxml import etree

from app.services import hwpx_edit as he
from app.services import office_edit

OPS = (
    "set_style", "format_text", "align", "move_paragraphs", "insert_table_column",
    "delete_table_column", "merge_cells", "split_cell", "insert_picture", "replace_picture",
    "page_setup",
)
RENUMBERS = frozenset({"move_paragraphs", "insert_picture"})

HP, HH, HC = he.HP, he.HH, "http://www.hancom.co.kr/hwpml/2011/core"
OPF = "http://www.idpf.org/2007/opf/"
ODF = "urn:oasis:names:tc:opendocument:xmlns:manifest"
_CONTENT, _MANIFEST = "Contents/content.hpf", "META-INF/manifest.xml"

#: the model's style names -> the house (`report_export`) paragraph and character shapes
STYLES = {"title": ("0", "2"), "heading 1": ("1", "3"), "heading 2": ("2", "4"),
          "heading 3": ("2", "4"), "body": ("3", "0")}
_ALIGN = {"left": "LEFT", "center": "CENTER", "right": "RIGHT", "justify": "JUSTIFY"}
#: `charPr` children in schema order: a new one goes where OWPML wants it
_CHAR_ORDER = ("fontRef", "ratio", "spacing", "relSz", "offset", "italic", "bold",
               "underline", "strikeout", "outline", "shadow", "emboss", "engrave",
               "supscript", "subscript")
_COLOUR = re.compile(r"#?([0-9A-Fa-f]{6})")
#: HWPUNIT per millimetre
_PER_MM = 7200 / 25.4


def _err(message: str) -> he.HwpxError:
    return he.HwpxError(message)


def _h(name: str) -> str:
    return f"{{{HH}}}{name}"


def _p(name: str) -> str:
    return f"{{{HP}}}{name}"


def _shape(head: etree._Element, kind: str, ident: str) -> etree._Element:
    found = next((e for e in he._listing(head, kind).iterchildren(_h(kind))
                  if e.get("id") == ident), None)
    if found is None:
        raise _err(f"문서에 {kind} {ident} 모양이 없습니다.")
    return found


def _put_child(parent: etree._Element, name: str, attrs: dict[str, str] | None) -> None:
    """`charPr` child `name` set (None: removed), at its place in schema order."""
    for stale in parent.findall(_h(name)):
        parent.remove(stale)
    if attrs is None:
        return
    made = etree.Element(_h(name), {k: v for k, v in attrs.items()})
    rank = _CHAR_ORDER.index(name)
    later = next((c for c in parent if isinstance(c.tag, str)
                  and c.tag.startswith(f"{{{HH}}}")
                  and c.tag.split("}")[1] in _CHAR_ORDER
                  and _CHAR_ORDER.index(c.tag.split("}")[1]) > rank), None)
    if later is not None:
        later.addprevious(made)
    else:
        parent.append(made)


def _where(doc: he._Doc, at: object) -> list[etree._Element]:
    text = str(at or "").strip()
    if re.fullmatch(r"t\d{1,4}\s{0,2}r\d{1,4}c\d{1,4}", text):
        return he._places(doc, text)
    return [he._paragraph(doc, text)[1]]


# ── spans ──


def _explode(paragraph: etree._Element) -> None:
    """Each run holding one `<hp:t>`: a span's ends can then fall between runs."""
    for run in list(paragraph.iterchildren(he._RUN)):
        texts = list(run.iterchildren(he._T))
        anchor = run
        for t in texts[1:]:
            made = etree.Element(he._RUN, dict(run.attrib))
            made.append(t)
            anchor.addnext(made)
            anchor = made


def _size(t: etree._Element) -> int:
    return len(t.text or "") + sum((1 if c.tag == he._LINEBREAK else 0) + len(c.tail or "")
                                    for c in t)


def _split_at(paragraph: etree._Element, at: int) -> None:
    count = 0
    for run in list(paragraph.iterchildren(he._RUN)):
        t = run.find(he._T)
        if t is None:
            continue
        size = _size(t)
        if count < at < count + size and len(t) == 0:
            cut = at - count
            twin = etree.Element(he._RUN, dict(run.attrib))
            etree.SubElement(twin, he._T).text = (t.text or "")[cut:]
            t.text = (t.text or "")[:cut]
            run.addnext(twin)
            return
        count += size


def _span_runs(paragraph: etree._Element, find: str) -> list[etree._Element]:
    _explode(paragraph)
    text = he._own_text(paragraph)
    runs = [r for r in paragraph.iterchildren(he._RUN) if r.find(he._T) is not None]
    if not find:
        return runs
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(find), text)]
    for start, end in spans:
        _split_at(paragraph, start)
        _split_at(paragraph, end)
    out, count = [], 0
    for run in paragraph.iterchildren(he._RUN):
        t = run.find(he._T)
        if t is None:
            continue
        size = _size(t)
        # a run the span only partly covers (one with a line break) is formatted whole
        if size and any(start < count + size and count < end for start, end in spans):
            out.append(run)
        count += size
    return out


def _format(op: dict) -> dict[str, Any]:
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
        out["color"] = "#" + found.group(1).upper()
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


def _char_variant(head: etree._Element, base: str, wanted: dict[str, Any]) -> str:
    made = copy.deepcopy(_shape(head, "charPr", base))
    for key in ("bold", "italic"):
        if key in wanted:
            _put_child(made, key, {} if wanted[key] else None)
    if "underline" in wanted:
        _put_child(made, "underline", {"type": "BOTTOM", "shape": "SOLID", "color": "#000000"}
                   if wanted["underline"] else None)
    if "color" in wanted:
        made.set("textColor", wanted["color"])
    if "size" in wanted:
        made.set("height", str(int(round(wanted["size"] * 100))))
    return he._define(head, "charPr", made)


def _para_variant(head: etree._Element, base: str, align: str) -> str:
    made = copy.deepcopy(_shape(head, "paraPr", base))
    place = made.find(_h("align"))
    if place is None:
        place = etree.Element(_h("align"), vertical="BASELINE")
        made.insert(0, place)
    place.set("horizontal", align)
    return he._define(head, "paraPr", made)


# ── tables ──


def _rows(table: etree._Element) -> list[etree._Element]:
    return list(table.iterchildren(he._TR))


def _addr(cell: etree._Element) -> etree._Element:
    return cell.find(_p("cellAddr"))


def _sz(cell: etree._Element) -> etree._Element:
    return cell.find(_p("cellSz"))


def _cell_at(row: etree._Element, col: int) -> etree._Element | None:
    return next((c for c in row.iterchildren(he._TC) if int(_addr(c).get("colAddr")) == col),
                None)


def _renumber(row: etree._Element) -> None:
    for c, cell in enumerate(row.iterchildren(he._TC)):
        _addr(cell).set("colAddr", str(c))


def _empty(cell: etree._Element) -> None:
    he._set_cell(cell, "", whole=True)


def _insert_column(table: etree._Element, after: int) -> None:
    for row in _rows(table):
        cell = _cell_at(row, after)
        fresh = copy.deepcopy(cell)
        _empty(fresh)
        fresh.set("header", cell.get("header", "0"))
        for one in (cell, fresh):
            size = _sz(one)
            size.set("width", str(int(size.get("width", "0")) // 2))
        cell.addnext(fresh)
        _renumber(row)
    table.set("colCnt", str(int(table.get("colCnt", "0")) + 1))


def _delete_column(table: etree._Element, col: int) -> None:
    for row in _rows(table):
        cell = _cell_at(row, col)
        gone = int(_sz(cell).get("width", "0"))
        row.remove(cell)
        cells = list(row.iterchildren(he._TC))
        near = cells[min(col, len(cells) - 1)]
        _sz(near).set("width", str(int(_sz(near).get("width", "0")) + gone))
        _renumber(row)
    table.set("colCnt", str(int(table.get("colCnt", "0")) - 1))


def _span(cell: etree._Element) -> tuple[int, int]:
    span = cell.find(_p("cellSpan"))
    return (int(span.get("rowSpan", "1")), int(span.get("colSpan", "1"))) if span is not None \
        else (1, 1)


def _merge(table: etree._Element, row: int, col: int, rows: int, cols: int) -> None:
    grid = _rows(table)
    if row + rows > len(grid):
        raise _err("merge_cells: 표 밖까지 합칠 수 없습니다.")
    anchor = _cell_at(grid[row], col)
    if anchor is None:
        raise _err(f"merge_cells: r{row}c{col} 칸이 없습니다.")
    cells = []
    for r in range(row, row + rows):
        for c in range(col, col + cols):
            cell = _cell_at(grid[r], c)
            if cell is None or _span(cell) != (1, 1):
                raise _err("merge_cells: 이미 병합된 칸과 겹치거나 표 밖입니다. 먼저 split_cell로 "
                           "나누세요.")
            cells.append((r, c, cell))
    width = sum(int(_sz(cell).get("width", "0")) for r, _, cell in cells if r == row)
    height = sum(int(_sz(cell).get("height", "0")) for _, c, cell in cells if c == col)
    sub = anchor.find(he._SUBLIST)
    for _, _, cell in cells:
        if cell is anchor:
            continue
        for paragraph in he._cell_paragraphs(cell):
            if he._own_text(paragraph).strip() or he._holds_more(paragraph):
                sub.append(paragraph)
        cell.getparent().remove(cell)
    span = anchor.find(_p("cellSpan"))
    span.set("rowSpan", str(rows))
    span.set("colSpan", str(cols))
    _sz(anchor).set("width", str(width))
    _sz(anchor).set("height", str(height))


def _split(table: etree._Element, row: int, col: int) -> None:
    grid = _rows(table)
    anchor = _cell_at(grid[row], col) if row < len(grid) else None
    if anchor is None:
        raise _err(f"split_cell: r{row}c{col} 칸이 없습니다.")
    rows, cols = _span(anchor)
    if (rows, cols) == (1, 1):
        raise _err(f"split_cell: r{row}c{col}은 병합된 칸이 아닙니다.")
    width = int(_sz(anchor).get("width", "0"))
    height = int(_sz(anchor).get("height", "0"))
    widths = [width // cols] * cols
    widths[-1] += width - sum(widths)
    heights = []
    for r in range(row, row + rows):
        other = next((c for c in grid[r].iterchildren(he._TC) if _span(c)[0] == 1), None)
        heights.append(int(_sz(other).get("height")) if other is not None else height // rows)
    span = anchor.find(_p("cellSpan"))
    span.set("rowSpan", "1")
    span.set("colSpan", "1")
    _sz(anchor).set("width", str(widths[0]))
    _sz(anchor).set("height", str(heights[0]))
    for r in range(row, row + rows):
        for c in range(col, col + cols):
            if (r, c) == (row, col):
                continue
            fresh = copy.deepcopy(anchor)
            _empty(fresh)
            _addr(fresh).set("rowAddr", str(r))
            _addr(fresh).set("colAddr", str(c))
            _sz(fresh).set("width", str(widths[c - col]))
            _sz(fresh).set("height", str(heights[r - row]))
            after = [x for x in grid[r].iterchildren(he._TC)
                     if int(_addr(x).get("colAddr")) < c]
            if after:
                after[-1].addnext(fresh)
            else:
                grid[r].insert(0, fresh)


# ── pictures ──


def _package(archive: Any, doc: he._Doc, name: str) -> etree._Element | None:
    if name not in doc.roots:
        try:
            doc.roots[name] = etree.fromstring(archive.read(name), he._PARSER)
        except (KeyError, etree.XMLSyntaxError):
            return None
    return doc.roots[name]


def _extension(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}.get(mime, "png")


def _add_binary(archive: Any, doc: he._Doc, mime: str, data: bytes) -> str:
    """The picture stored in `BinData/` and listed in the package; its item id."""
    content = _package(archive, doc, _CONTENT)
    if content is None:
        raise _err("문서 목록(content.hpf)을 읽지 못해 그림을 넣을 수 없습니다.")
    manifest = content.find(f"{{{OPF}}}manifest")
    taken = {item.get("id") for item in manifest.iter(f"{{{OPF}}}item")}
    taken |= {n.rsplit("/", 1)[-1].split(".")[0] for n in archive.namelist() + list(doc.added)
              if n.startswith("BinData/")}
    number = next(n for n in range(1, 10_000) if f"image{n}" not in taken)
    ident, extension = f"image{number}", _extension(mime)
    path = f"BinData/{ident}.{extension}"
    media = f"image/{'jpg' if extension == 'jpg' else extension}"
    item = etree.Element(f"{{{OPF}}}item", id=ident, href=path)
    item.set("media-type", media)
    item.set("isEmbeded", "1")
    first = manifest.find(f"{{{OPF}}}item")
    if first is not None:
        first.addprevious(item)
    else:
        manifest.append(item)
    listing = _package(archive, doc, _MANIFEST)
    if listing is not None:
        entry = etree.SubElement(listing, f"{{{ODF}}}file-entry")
        entry.set(f"{{{ODF}}}full-path", path)
        entry.set(f"{{{ODF}}}media-type", media)
    doc.added[path] = data
    return ident


def _pixels(data: bytes) -> tuple[int, int]:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as picture:
            return picture.size
    except Exception as exc:  # noqa: BLE001
        raise _err("그림 파일을 읽지 못했습니다.") from exc


def _picture_paragraph(archive: Any, doc: he._Doc, mime: str, data: bytes) -> etree._Element:
    from app.services import report_export

    _pixels(data)
    ident = _add_binary(archive, doc, mime, data)
    number = int(ident.removeprefix("image"))
    xml = report_export._hwpx_picture(number, data).replace(f'"image{number}"', f'"{ident}"')
    made = etree.fromstring(
        f'<w xmlns:hp="{HP}" xmlns:hc="{HC}">{xml}</w>', he._PARSER)[0]
    # the house centred figure paragraph and body text, under the file's own ids
    shapes = he._import_shapes(he._header(archive, doc),
                               {"paraPr": ("5",), "charPr": ("0",)})
    return he._remapped(made, shapes)


def _replace_picture(archive: Any, doc: he._Doc, paragraph: etree._Element, mime: str,
                     data: bytes) -> None:
    pic = paragraph.find(f".//{_p('pic')}")
    img = pic.find(f".//{{{HC}}}img") if pic is not None else None
    if img is None:
        raise _err("replace_picture: 그 문단에는 그림이 없습니다. insert_picture를 쓰세요.")
    wide, high = _pixels(data)
    img.set("binaryItemIDRef", _add_binary(archive, doc, mime, data))
    current = pic.find(_p("curSz"))
    width = int(current.get("width", "0") or 0)
    height = int(width * high / max(1, wide))
    native_w, native_h = wide * 75, high * 75
    for name in ("curSz", "sz"):
        for node in pic.iter(_p(name)):
            node.set("width", str(width))
            node.set("height", str(height))
    for node in pic.iter(_p("orgSz")):
        node.set("width", str(width))
        node.set("height", str(height))
    rect = pic.find(_p("imgRect"))
    if rect is not None:
        points = {"pt0": (0, 0), "pt1": (width, 0), "pt2": (width, height), "pt3": (0, height)}
        for name, (x, y) in points.items():
            node = rect.find(f"{{{HC}}}{name}")
            if node is not None:
                node.set("x", str(x))
                node.set("y", str(y))
    clip = pic.find(_p("imgClip"))
    if clip is not None:
        clip.set("right", str(native_w))
        clip.set("bottom", str(native_h))
    dim = pic.find(_p("imgDim"))
    if dim is not None:
        dim.set("dimwidth", str(native_w))
        dim.set("dimheight", str(native_h))
    rotation = pic.find(_p("rotationInfo"))
    if rotation is not None:
        rotation.set("centerX", str(width // 2))
        rotation.set("centerY", str(height // 2))


# ── page setup ──


def _sec(doc: he._Doc) -> tuple[etree._Element, etree._Element]:
    """The first section's settings and the run that holds them."""
    for paragraph in doc.paragraphs[:3]:
        for run in paragraph.iterchildren(he._RUN):
            sec = run.find(_p("secPr"))
            if sec is not None:
                return sec, run
    raise _err("page_setup: 쪽 설정(secPr)이 없는 문서입니다.")


def _story(run: etree._Element, kind: str, text: str, head: etree._Element) -> None:
    """A header or footer control with `text`, the one there replaced."""
    for ctrl in list(run.iterchildren(_p("ctrl"))):
        if ctrl.find(_p(kind)) is not None:
            run.remove(ctrl)
    if not text:
        return
    shapes = he._import_shapes(head, {"paraPr": ("3",), "charPr": ("5",)})
    para = (f'<hp:p paraPrIDRef="{shapes["paraPr"]["3"]}" styleIDRef="0">'
            f'<hp:run charPrIDRef="{shapes["charPr"]["5"]}"><hp:t/></hp:run></hp:p>')
    xml = (f'<hp:ctrl xmlns:hp="{HP}"><hp:{kind} id="{10001 if kind == "header" else 10002}"'
           ' applyPageType="BOTH"><hp:subList id="" textDirection="HORIZONTAL"'
           ' lineWrap="BREAK" vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0"'
           ' textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
           f"{para}</hp:subList></hp:{kind}></hp:ctrl>")
    ctrl = etree.fromstring(xml, he._PARSER)
    ctrl.find(f".//{he._T}").text = text
    sec = run.find(_p("secPr"))
    (sec if sec is not None else run[0]).addnext(ctrl)


def _page(archive: Any, doc: he._Doc, op: dict) -> str:
    sec, run = _sec(doc)
    page = sec.find(_p("pagePr"))
    said = []
    orientation = op.get("orientation")
    if orientation not in (None, ""):
        if orientation not in ("portrait", "landscape"):
            raise _err("page_setup: orientation은 portrait 또는 landscape입니다.")
        # the paper keeps its size; 한글 turns it by the flag (WIDELY 세로, NARROWLY 가로)
        page.set("landscape", "NARROWLY" if orientation == "landscape" else "WIDELY")
        said.append("가로" if orientation == "landscape" else "세로")
    margins = op.get("margins_mm")
    if isinstance(margins, dict) and margins:
        margin = page.find(_p("margin"))
        for side, value in margins.items():
            if side not in ("top", "bottom", "left", "right"):
                raise _err("page_setup: margins_mm은 top·bottom·left·right입니다.")
            try:
                mm = float(value)
            except (TypeError, ValueError) as exc:
                raise _err("page_setup: 여백은 mm 숫자입니다.") from exc
            if not 0 <= mm <= 60:
                raise _err("page_setup: 여백은 0~60mm입니다.")
            margin.set(side, str(int(round(mm * _PER_MM))))
        said.append("여백 " + ", ".join(f"{k} {v}mm" for k, v in margins.items()))
    head = he._header(archive, doc)
    for where in ("header", "footer"):
        if op.get(where) is not None:
            text = office_edit.text_arg(op, where, he.HwpxError)
            _story(run, where, text, head)
            name = "머리글" if where == "header" else "바닥글"
            said.append(f"{name} 「{office_edit.short(text)}」")
    if op.get("page_numbers") is not None:
        if not isinstance(op["page_numbers"], bool):
            raise _err("page_setup: page_numbers는 true나 false입니다.")
        for ctrl in list(run.iterchildren(_p("ctrl"))):
            if ctrl.find(_p("pageNum")) is not None:
                run.remove(ctrl)
        if op["page_numbers"]:
            ctrl = etree.SubElement(run, _p("ctrl"))
            etree.SubElement(ctrl, _p("pageNum"), pos="BOTTOM_CENTER", formatType="DIGIT",
                             sideChar="")
            text = run.find(he._T)
            if text is not None:
                text.addprevious(ctrl)
        said.append("쪽 번호 " + ("넣음" if op["page_numbers"] else "뺌"))
    if not said:
        raise _err("page_setup: orientation, margins_mm, header, footer, page_numbers 중 하나는 "
                   "주세요.")
    return "쪽 설정: " + ", ".join(said)


# ── resolve and run ──


def _ints(op: dict, *names: str) -> list[int]:
    try:
        return [int(op.get(n) if op.get(n) is not None else 1) for n in names]
    except (TypeError, ValueError) as exc:
        raise _err(f"{op.get('op')}: {'·'.join(names)}는 숫자입니다.") from exc


def resolve(archive: Any, doc: he._Doc, op: dict,
            pictures: dict[str, tuple[str, bytes]]) -> Any:
    kind = op.get("op")
    if kind == "set_style":
        style = " ".join(str(op.get("style") or "").lower().split())
        if style not in STYLES:
            raise _err(f"set_style: style은 {', '.join(STYLES)} 중 하나입니다.")
        return he._paragraph(doc, op.get("id"))[1], style
    if kind == "format_text":
        places = _where(doc, op.get("id"))
        find = str(op.get("find") or "")
        if find and not any(find in he._own_text(p) for p in places):
            raise _err(f"format_text: {op.get('id')}에 「{find}」가 없습니다.")
        return places, find, _format(op)
    if kind == "align":
        if op.get("align") not in _ALIGN:
            raise _err(f"align: align은 {', '.join(_ALIGN)} 중 하나입니다.")
        return _where(doc, op.get("id"))
    if kind == "move_paragraphs":
        i, _ = he._paragraph(doc, op.get("from"))
        j, _ = he._paragraph(doc, op.get("to") or op.get("from"))
        if j < i:
            raise _err("move_paragraphs: to가 from보다 앞입니다.")
        moving = doc.paragraphs[i:j + 1]
        if any(p.find(f".//{_p('secPr')}") is not None for p in moving):
            raise _err("move_paragraphs: 쪽 설정이 있는 첫 문단은 옮길 수 없습니다.")
        after = str(op.get("after") or "").strip()
        if after == "p0":
            raise _err("move_paragraphs: 첫 문단(쪽 설정) 앞으로는 옮길 수 없습니다. after:'p1'.")
        k, anchor = he._paragraph(doc, after)
        if i <= k <= j:
            raise _err("move_paragraphs: 옮길 범위 안으로는 옮길 수 없습니다.")
        return moving, anchor
    if kind in ("insert_table_column", "delete_table_column"):
        table = he._table(doc, op.get("table"))
        if he._merged(table):
            raise _err(f"{kind}: 병합된 칸이 있는 표의 열은 바꿀 수 없습니다. 먼저 split_cell로 "
                       "나누세요.")
        (col,) = _ints(op, "after" if kind == "insert_table_column" else "col")
        cols = he._shape(table)[1]
        if not 0 <= col < cols:
            raise _err(f"{kind}: c{col} 열이 없습니다 (0~{cols - 1}).")
        if kind == "delete_table_column" and cols < 2:
            raise _err("delete_table_column: 열이 하나뿐인 표의 열은 지울 수 없습니다.")
        if kind == "insert_table_column" and cols >= office_edit.MAX_TABLE_COLS:
            raise _err(f"insert_table_column: 표는 {office_edit.MAX_TABLE_COLS}칸까지입니다.")
        return table, col
    if kind in ("merge_cells", "split_cell"):
        table = he._table(doc, op.get("table"))
        row, col, rows, cols = _ints(op, "row", "col", "span_rows", "span_cols")
        if kind == "merge_cells" and rows * cols < 2:
            raise _err("merge_cells: span_rows나 span_cols로 둘 이상의 칸을 주세요.")
        return table, row, col, rows, cols
    if kind in ("insert_picture", "replace_picture"):
        name = str(op.get("file") or "").strip()
        if name not in pictures:
            raise _err(f"{kind}: 그림 파일 「{name}」을 찾지 못했습니다. 이 대화에 첨부했거나 "
                       "만든 PNG·JPEG 그림의 이름이나 id를 주세요.")
        mime, data = pictures[name]
        if kind == "insert_picture":
            return he._paragraph(doc, op.get("after"))[1], mime, data
        return he._paragraph(doc, op.get("id"))[1], mime, data
    if kind == "page_setup":
        _sec(doc)
        return None
    raise office_edit.unknown_op(kind, he.HwpxError)


def run(archive: Any, doc: he._Doc, op: dict, target: Any) -> str:
    kind = op["op"]
    if kind == "set_style":
        paragraph, style = target
        para, char = STYLES[style]
        shapes = he._import_shapes(he._header(archive, doc), {"paraPr": (para,),
                                                               "charPr": (char,)})
        paragraph.set("paraPrIDRef", shapes["paraPr"][para])
        for one in paragraph.iterchildren(he._RUN):
            one.set("charPrIDRef", shapes["charPr"][char])
        return f"{op['id']} 스타일 → {style}"
    if kind == "format_text":
        places, find, wanted = target
        head = he._header(archive, doc)
        made: dict[str, str] = {}
        for paragraph in places:
            for one in _span_runs(paragraph, find):
                base = one.get("charPrIDRef", "0")
                if base not in made:
                    made[base] = _char_variant(head, base, wanted)
                one.set("charPrIDRef", made[base])
        what = f"「{office_edit.short(find)}」" if find else "문단 전체"
        return f"{op['id']} {what} 글자 모양: " + ", ".join(f"{k}={v}" for k, v in wanted.items())
    if kind == "align":
        head = he._header(archive, doc)
        for paragraph in target:
            paragraph.set("paraPrIDRef", _para_variant(
                head, paragraph.get("paraPrIDRef", "0"), _ALIGN[op["align"]]))
        return f"{op['id']} 정렬 → {op['align']}"
    if kind == "move_paragraphs":
        moving, anchor = target
        for paragraph in moving:
            paragraph.getparent().remove(paragraph)
        for paragraph in reversed(moving):
            anchor.addnext(paragraph)
        span = f"{op['from']}~{op.get('to') or op['from']}"
        return f"{span}를 {op['after']} 뒤로 옮김"
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
        anchor, mime, data = target
        anchor.addnext(_picture_paragraph(archive, doc, mime, data))
        return f"{op['after']} 뒤에 그림 「{op['file']}」 넣음"
    if kind == "replace_picture":
        paragraph, mime, data = target
        _replace_picture(archive, doc, paragraph, mime, data)
        return f"{op['id']} 그림을 「{op['file']}」로 바꿈"
    if kind == "page_setup":
        return _page(archive, doc, op)
    raise office_edit.unknown_op(kind, he.HwpxError)
