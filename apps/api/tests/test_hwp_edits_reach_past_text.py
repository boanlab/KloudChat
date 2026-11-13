"""HWP chat edits past text: a style, a span's format, alignment, moving a chapter, table
columns and merges, pictures and page setup. A changed shape is a new shape of the file's own,
never the shared one changed in place; the outline reads the result back."""

from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree
from PIL import Image

from app.services import hwp_doc, hwpx_edit

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"

BODY = """1장 개요

개요 본문입니다. 예산은 3억 원입니다.

2장 위험

위험 본문입니다.

| 구분 | 내용 |
|---|---|
| 일정 | 3월 |
| 장소 | 본관 |"""


def _png(width=200, height=100) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (30, 30, 200)).save(out, "PNG")
    return out.getvalue()


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {n: archive.read(n) for n in archive.namelist()}


def _head(data: bytes) -> etree._Element:
    return etree.fromstring(_parts(data)["Contents/header.xml"])


def _section(data: bytes) -> etree._Element:
    return etree.fromstring(_parts(data)["Contents/section0.xml"])


def _pid(data: bytes, starts: str) -> str:
    return next(line.split(":")[0].split(" ")[0] for line in hwpx_edit.outline(data).splitlines()
                if line.split(": ", 1)[-1].startswith(starts))


def _p(data: bytes, starts: str) -> etree._Element:
    return next(p for p in _section(data).iter(f"{HP}p")
                if "".join(t.text or "" for t in p.iter(f"{HP}t")).startswith(starts))


def test_a_span_format_is_a_new_shape_and_the_shared_one_stays():
    data = hwp_doc.plain("안내", BODY)
    pid = _pid(data, "개요 본문")
    before = {s.get("id"): etree.tostring(s) for s in _head(data).iter(f"{HH}charPr")}
    edited = hwpx_edit.apply(data, [
        {"op": "format_text", "id": pid, "find": "3억 원", "bold": True, "color": "#C00000"},
        {"op": "align", "id": pid, "align": "center"},
    ]).data
    paragraph = _p(edited, "개요 본문")
    runs = [(r.get("charPrIDRef"), "".join(t.text or "" for t in r.iter(f"{HP}t")))
            for r in paragraph.iterchildren(f"{HP}run")]
    texts = [t for _, t in runs]
    assert "3억 원" in texts and "".join(texts) == "개요 본문입니다. 예산은 3억 원입니다."
    head = _head(edited)
    shapes = {s.get("id"): s for s in head.iter(f"{HH}charPr")}
    red = shapes[next(c for c, t in runs if t == "3억 원")]
    assert red.get("textColor") == "#C00000" and red.find(f"{HH}bold") is not None
    # the shapes other paragraphs use are as they were
    for ident, xml in before.items():
        assert etree.tostring(shapes[ident]) == xml
    align = next(p for p in head.iter(f"{HH}paraPr")
                 if p.get("id") == paragraph.get("paraPrIDRef")).find(f"{HH}align")
    assert align.get("horizontal") == "CENTER"


def test_a_heading_style_and_a_chapter_move():
    data = hwp_doc.plain("안내", BODY)
    styled = hwpx_edit.apply(data, [
        {"op": "set_style", "id": _pid(data, "1장 개요"), "style": "heading 1"},
        {"op": "set_style", "id": _pid(data, "2장 위험"), "style": "heading 1"},
    ]).data
    assert "(heading 1): 1장 개요" in hwpx_edit.outline(styled)
    moved = hwpx_edit.apply(styled, [{
        "op": "move_paragraphs", "from": _pid(styled, "2장 위험"),
        "to": _pid(styled, "위험 본문"), "after": _pid(styled, "안내"),
    }]).data
    order = [line.split(": ", 1)[-1] for line in hwpx_edit.outline(moved).splitlines()
             if line.startswith("p")]
    assert order.index("2장 위험") < order.index("1장 개요")
    assert order.index("위험 본문입니다.") < order.index("1장 개요")


def test_table_columns_merges_and_splits():
    data = hwp_doc.plain("안내", BODY)
    wider = hwpx_edit.apply(data, [{"op": "insert_table_column", "table": "t1", "after": 0}]).data
    assert "(표 3×3" in hwpx_edit.outline(wider)
    narrower = hwpx_edit.apply(wider, [{"op": "delete_table_column", "table": "t1",
                                        "col": 1}]).data
    assert "r0: 구분 | 내용" in hwpx_edit.outline(narrower)
    merged = hwpx_edit.apply(narrower, [{"op": "merge_cells", "table": "t1", "row": 1,
                                         "col": 0, "span_rows": 2}]).data
    text = hwpx_edit.outline(merged)
    assert "병합 셀 있음" in text and "일정 / 장소" in text
    split = hwpx_edit.apply(merged, [{"op": "split_cell", "table": "t1", "row": 1,
                                      "col": 0}]).data
    assert "병합 셀 있음" not in hwpx_edit.outline(split)
    table = next(_section(split).iter(f"{HP}tbl"))
    assert len(list(table.iter(f"{HP}tc"))) == 6


def test_a_picture_goes_in_with_its_file_and_is_replaced():
    data = hwp_doc.plain("안내", BODY)
    pictures = {"chart.png": ("image/png", _png()), "sq.png": ("image/png", _png(100, 100))}
    edited = hwpx_edit.apply(data, [
        {"op": "insert_picture", "after": _pid(data, "개요 본문"), "file": "chart.png"},
    ], pictures=pictures).data
    parts = _parts(edited)
    binary = [n for n in parts if n.startswith("BinData/")]
    assert binary and parts[binary[0]] == pictures["chart.png"][1]
    assert binary[0].encode() in parts["Contents/content.hpf"]
    pid = _pid(edited, "(그림)")
    again = hwpx_edit.apply(edited, [{"op": "replace_picture", "id": pid, "file": "sq.png"}],
                            pictures=pictures).data
    pic = next(_section(again).iter(f"{HP}pic"))
    size = pic.find(f"{HP}curSz")
    assert size.get("width") == size.get("height")
    with pytest.raises(hwpx_edit.HwpxError, match="찾지 못했습니다"):
        hwpx_edit.apply(data, [{"op": "insert_picture", "after": "p1", "file": "x.png"}])


def test_page_setup():
    data = hwp_doc.plain("안내", BODY)
    edited = hwpx_edit.apply(data, [{
        "op": "page_setup", "orientation": "landscape", "margins_mm": {"left": 25},
        "footer": "기획팀", "page_numbers": True,
    }]).data
    section = _section(edited)
    page = next(section.iter(f"{HP}pagePr"))
    assert page.get("landscape") == "NARROWLY"
    assert page.find(f"{HP}margin").get("left") == str(round(25 * 7200 / 25.4))
    assert next(section.iter(f"{HP}pageNum")) is not None
    footer = next(section.iter(f"{HP}footer"))
    assert "기획팀" in "".join(footer.itertext())
