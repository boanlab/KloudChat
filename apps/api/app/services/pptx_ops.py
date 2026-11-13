"""PowerPoint edit ops past text (`pptx_edit.apply` resolves and runs them like its own): a
native table put on a slide (an `a:tbl` PowerPoint edits as a table, never rows of text in a
box), table columns added or taken away, pictures put in or replaced, and a slide moved to
another layout with its content kept where it stood.
"""

from __future__ import annotations

import copy
import io
from typing import Any

from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from app.services import office_edit
from app.services import pptx_edit as pe

OPS = ("insert_table", "insert_table_column", "delete_table_column", "insert_picture",
       "replace_picture", "change_layout")
#: ops after which a slide's shape numbers are not the ones read
RENUMBERS = frozenset({"insert_table", "insert_picture", "change_layout"})

#: a table's text size: fits a dozen rows on a 16:9 slide
_TABLE_PT = 14


def _err(message: str) -> pe.PptxError:
    return pe.PptxError(message)


def _area(deck: pe._Deck, entry: pe._Slide, replace: pe._Item | None) -> tuple[int, ...]:
    """Where a table or picture goes: the shape it replaces, else under the slide's title,
    else the slide's middle; (left, top, width, height) in EMU."""
    prs = deck.prs
    if replace is not None:
        shape = replace.shape
        return int(shape.left or 0), int(shape.top or 0), int(shape.width or 0), \
            int(shape.height or 0)
    width, height = int(prs.slide_width), int(prs.slide_height)
    margin = width // 20
    top = height // 5
    for item in entry.items:
        if item.mark.startswith("제목"):
            shape = item.shape
            top = int(shape.top or 0) + int(shape.height or 0) + height // 40
            break
    return margin, top, width - 2 * margin, max(height // 4, height - top - height // 12)


def _table(deck: pe._Deck, entry: pe._Slide, rows: list[list[str]],
           replace: pe._Item | None) -> None:
    left, top, width, height = _area(deck, entry, replace)
    if replace is not None:
        replace.shape._element.getparent().remove(replace.shape._element)
    row_height = Emu(int(Pt(_TABLE_PT * 2.2)))
    frame = entry.slide.shapes.add_table(
        len(rows), len(rows[0]), Emu(left), Emu(top), Emu(width),
        Emu(min(height, row_height * len(rows))))
    table = frame.table
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = table.cell(r, c)
            cell.text = text
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(_TABLE_PT)
                    if r == 0:
                        run.font.bold = True


def _insert_column(item: pe._Item, after: int) -> None:
    tbl = pe._tbl(item.shape)
    grid = tbl.find(qn("a:tblGrid"))
    cols = list(grid.iterchildren(qn("a:gridCol")))
    half = int(cols[after].get("w", "0")) // 2
    cols[after].set("w", str(half))
    made = copy.deepcopy(cols[after])
    for ext in list(made):
        made.remove(ext)
    cols[after].addnext(made)
    for row in pe._rows(tbl):
        cell = list(row.iterchildren(pe._A_TC))[after]
        fresh = copy.deepcopy(cell)
        pe._set_lines(pe._cell_body(fresh), "")
        cell.addnext(fresh)


def _delete_column(item: pe._Item, col: int) -> None:
    tbl = pe._tbl(item.shape)
    grid = tbl.find(qn("a:tblGrid"))
    cols = list(grid.iterchildren(qn("a:gridCol")))
    gone = int(cols[col].get("w", "0"))
    grid.remove(cols[col])
    rest = list(grid.iterchildren(qn("a:gridCol")))
    near = rest[min(col, len(rest) - 1)]
    near.set("w", str(int(near.get("w", "0")) + gone))
    for row in pe._rows(tbl):
        row.remove(list(row.iterchildren(pe._A_TC))[col])


def _pixels(data: bytes) -> tuple[int, int]:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as picture:
            return picture.size
    except Exception as exc:  # noqa: BLE001
        raise _err("그림 파일을 읽지 못했습니다.") from exc


def _picture(deck: pe._Deck, entry: pe._Slide, data: bytes, replace: pe._Item | None) -> None:
    left, top, width, height = _area(deck, entry, replace)
    if replace is not None:
        replace.shape._element.getparent().remove(replace.shape._element)
    wide, high = _pixels(data)
    # inside the box, its own shape kept, centred
    scale = min(width / max(1, wide), height / max(1, high))
    w, h = int(wide * scale), int(high * scale)
    entry.slide.shapes.add_picture(io.BytesIO(data), Emu(left + (width - w) // 2),
                                   Emu(top + (height - h) // 2), Emu(w), Emu(h))


def _replace_picture(item: pe._Item, entry: pe._Slide, data: bytes) -> None:
    blip = item.shape._element.find(".//" + qn("a:blip"))
    if blip is None:
        raise _err("replace_picture: 그림이 아닙니다.")
    _, rid = entry.slide.part.get_or_add_image_part(io.BytesIO(data))
    blip.set(qn("r:embed"), rid)
    wide, high = _pixels(data)
    shape = item.shape
    shape.height = Emu(int(int(shape.width or 0) * high / max(1, wide)))
    # a crop made for the old picture does not fit the new one
    for crop in item.shape._element.iter(qn("a:srcRect")):
        crop.getparent().remove(crop)


def _layout(deck: pe._Deck, name: str):
    wanted = " ".join(str(name or "").split()).casefold()
    layouts = [lay for master in deck.prs.slide_masters for lay in master.slide_layouts]
    found = next((lay for lay in layouts if (lay.name or "").casefold() == wanted), None)
    if found is None:
        names = ", ".join(f"「{lay.name}」" for lay in layouts if lay.name)
        raise _err(f"change_layout: 레이아웃 「{name}」이 없습니다. 이 파일의 레이아웃: {names}")
    return found


def _change_layout(entry: pe._Slide, layout) -> None:
    """The slide on `layout`; every shape keeps the place it is drawn at now (a placeholder
    that took its place from the old layout gets it written down first)."""
    slide = entry.slide
    for shape in slide.shapes:
        if not shape.is_placeholder:
            continue
        sp = shape._element
        props = sp.find(qn("p:spPr"))
        if props is not None and props.find(qn("a:xfrm")) is None:
            left, top, width, height = shape.left, shape.top, shape.width, shape.height
            if None not in (left, top, width, height):
                shape.left, shape.top, shape.width, shape.height = left, top, width, height
    for rel in slide.part.rels.values():
        if rel.reltype == RT.SLIDE_LAYOUT:
            rel._target = layout.part
            break


def resolve(deck: pe._Deck, op: dict, pictures: dict[str, tuple[str, bytes]]) -> Any:
    kind = op.get("op")
    if kind == "insert_table":
        rows = office_edit.table_arg(op, pe.PptxError)
        index = pe._slide_ref(deck, op.get("slide"))
        entry = deck.slides[index]
        replace = (pe._item(deck, op.get("replace"), ("text", "picture"))
                   if op.get("replace") not in (None, "") else None)
        return entry, rows, replace
    if kind in ("insert_table_column", "delete_table_column"):
        item = pe._item(deck, op.get("table"), ("table",))
        tbl = pe._tbl(item.shape)
        if pe._merged(tbl):
            raise _err(f"{kind}: 병합된 칸이 있는 표의 열은 바꿀 수 없습니다.")
        name = "after" if kind == "insert_table_column" else "col"
        try:
            col = int(op.get(name))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise _err(f"{kind}: {name}은 0부터 세는 숫자입니다.") from exc
        cols = pe._shape_of(tbl)[1]
        if not 0 <= col < cols:
            raise _err(f"{kind}: c{col} 열이 없습니다 (0~{cols - 1}).")
        if kind == "delete_table_column" and cols < 2:
            raise _err("delete_table_column: 열이 하나뿐인 표의 열은 지울 수 없습니다.")
        if kind == "insert_table_column" and cols >= office_edit.MAX_TABLE_COLS:
            raise _err(f"insert_table_column: 표는 {office_edit.MAX_TABLE_COLS}칸까지입니다.")
        return item, col
    if kind in ("insert_picture", "replace_picture"):
        name = str(op.get("file") or "").strip()
        if name not in pictures:
            raise _err(f"{kind}: 그림 파일 「{name}」을 찾지 못했습니다. 이 대화에 첨부했거나 "
                       "만든 PNG·JPEG 그림의 이름이나 id를 주세요.")
        data = pictures[name][1]
        if kind == "insert_picture":
            entry = deck.slides[pe._slide_ref(deck, op.get("slide"))]
            replace = (pe._item(deck, op.get("replace"), ("text", "picture"))
                       if op.get("replace") not in (None, "") else None)
            return entry, data, replace
        item = pe._item(deck, op.get("id"), ("picture",))
        entry = next(e for e in deck.slides if item in e.items)
        return item, entry, data
    if kind == "change_layout":
        entry = deck.slides[pe._slide_ref(deck, op.get("slide"))]
        return entry, _layout(deck, str(op.get("layout") or ""))
    raise _err(f"모르는 op {kind!r}")


def run(deck: pe._Deck, op: dict, target: Any) -> str:
    kind = op["op"]
    if kind == "insert_table":
        entry, rows, replace = target
        _table(deck, entry, rows, replace)
        where = f"{op['replace']} 자리에" if replace is not None else f"{op['slide']}에"
        return f"{where} 표 추가 ({len(rows)}줄 × {len(rows[0])}칸)"
    if kind == "insert_table_column":
        item, col = target
        _insert_column(item, col)
        return f"{op['table']} c{col} 오른쪽에 열 추가"
    if kind == "delete_table_column":
        item, col = target
        _delete_column(item, col)
        return f"{op['table']} c{col} 열 삭제"
    if kind == "insert_picture":
        entry, data, replace = target
        _picture(deck, entry, data, replace)
        where = f"{op['replace']} 자리에" if replace is not None else f"{op['slide']}에"
        return f"{where} 그림 「{op['file']}」 넣음"
    if kind == "replace_picture":
        item, entry, data = target
        _replace_picture(item, entry, data)
        return f"{op['id']} 그림을 「{op['file']}」로 바꿈"
    if kind == "change_layout":
        entry, layout = target
        _change_layout(entry, layout)
        return f"{op['slide']} 레이아웃 → 「{layout.name}」"
    raise _err(f"모르는 op {kind!r}")
