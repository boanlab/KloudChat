"""Word chat edits past text: a style and a heading level, a span's format, alignment, moving
a chapter, table columns and merges, pictures and page setup; each keeps what it was not asked
to change, and the result opens in python-docx again."""

from __future__ import annotations

import io

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from PIL import Image

from app.services import docx_edit


def _png(width=200, height=100) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def _doc() -> bytes:
    doc = Document()
    doc.add_paragraph("1장 개요")
    doc.add_paragraph("개요 본문입니다. 예산은 3억 원입니다.")
    doc.add_paragraph("2장 위험")
    doc.add_paragraph("위험 본문입니다.")
    table = doc.add_table(rows=3, cols=2)
    for r, row in enumerate([["구분", "내용"], ["일정", "3월"], ["장소", "본관"]]):
        for c, text in enumerate(row):
            table.cell(r, c).text = text
    doc.add_paragraph("끝.")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _open(data: bytes):
    return Document(io.BytesIO(data))


def test_style_format_and_alignment():
    data = docx_edit.apply(_doc(), [
        {"op": "set_style", "id": "p1", "style": "heading 1"},
        {"op": "format_text", "id": "p2", "find": "3억 원", "bold": True, "color": "#C00000"},
        {"op": "align", "id": "p3", "align": "center"},
    ]).data
    doc = _open(data)
    assert doc.paragraphs[0].style.name == "Heading 1"
    runs = [(r.text, bool(r.bold)) for r in doc.paragraphs[1].runs]
    assert ("3억 원", True) in runs and ("개요 본문입니다. 예산은 ", False) in runs
    red = next(r for r in doc.paragraphs[1].runs if r.text == "3억 원")
    assert str(red.font.color.rgb) == "C00000"
    assert doc.paragraphs[2].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert "(heading 1)" in docx_edit.outline(data)


def test_a_chapter_moves_with_its_body():
    data = docx_edit.apply(_doc(), [
        {"op": "move_paragraphs", "from": "p3", "to": "p4", "after": "p0"},
    ]).data
    texts = [p.text for p in _open(data).paragraphs]
    assert texts[:4] == ["2장 위험", "위험 본문입니다.", "1장 개요",
                         "개요 본문입니다. 예산은 3억 원입니다."]


def test_table_columns_and_merges():
    data = docx_edit.apply(_doc(), [{"op": "insert_table_column", "table": "t1", "after": 0}]).data
    assert "r0: 구분 |  | 내용" in docx_edit.outline(data)
    data = docx_edit.apply(data, [
        {"op": "set_cell", "table": "t1", "row": 0, "col": 1, "text": "비고"}]).data
    table = _open(data).tables[0]
    assert [c.text for c in table.rows[0].cells] == ["구분", "비고", "내용"]
    data = docx_edit.apply(data, [{"op": "delete_table_column", "table": "t1", "col": 1}]).data
    assert [c.text for c in _open(data).tables[0].rows[0].cells] == ["구분", "내용"]
    merged = docx_edit.apply(data, [
        {"op": "merge_cells", "table": "t1", "row": 1, "col": 0, "span_rows": 2},
    ]).data
    assert "병합 셀 있음" in docx_edit.outline(merged)
    assert "일정" in docx_edit.outline(merged) and "장소" in docx_edit.outline(merged)
    split = docx_edit.apply(merged, [{"op": "split_cell", "table": "t1", "row": 1, "col": 0}]).data
    assert "병합 셀 있음" not in docx_edit.outline(split)


def test_pictures_go_in_and_are_replaced():
    pictures = {"chart.png": ("image/png", _png()), "new.png": ("image/png", _png(100, 100))}
    data = docx_edit.apply(_doc(), [
        {"op": "insert_picture", "after": "p2", "file": "chart.png", "width_mm": 80},
    ], pictures=pictures).data
    outline = docx_edit.outline(data)
    assert "p3 (picture): (그림)" in outline
    replaced = docx_edit.apply(data, [
        {"op": "replace_picture", "id": "p3", "file": "new.png"},
    ], pictures=pictures).data
    doc = _open(replaced)
    shape = doc.inline_shapes[0]
    assert abs(shape.width - shape.height) < 2000  # square now, width kept
    with pytest.raises(docx_edit.DocxError, match="찾지 못했습니다"):
        docx_edit.apply(data, [{"op": "insert_picture", "after": "p1", "file": "x.png"}],
                        pictures=pictures)


def test_page_setup():
    data = docx_edit.apply(_doc(), [{
        "op": "page_setup", "orientation": "landscape", "margins_mm": {"left": 20},
        "header": "기획팀", "page_numbers": True,
    }]).data
    section = _open(data).sections[0]
    assert section.page_width > section.page_height
    assert round(section.left_margin.mm) == 20
    assert section.header.paragraphs[0].text == "기획팀"
    assert b"PAGE" in _footer_xml(data)


def _footer_xml(data: bytes) -> bytes:
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return b"".join(archive.read(n) for n in archive.namelist() if "footer" in n)
