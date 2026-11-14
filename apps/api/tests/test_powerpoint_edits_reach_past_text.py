"""PowerPoint chat edits past text: a real table on a slide (never rows of text), table
columns, pictures put in or replaced, and a layout change that keeps the slide's content; the
outline counts slides in order, not by a printed number."""

from __future__ import annotations

import io

from PIL import Image
from pptx import Presentation

from app.services import pptx_doc, pptx_edit


def _png(width=200, height=100) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (30, 120, 30)).save(out, "PNG")
    return out.getvalue()


def _deck() -> bytes:
    return pptx_doc.deck("사업 계획", "기획팀", [
        {"title": "현황", "bullets": ["매출 120억", "고객 3만"]},
        {"title": "일정", "rows": [["단계", "시기"], ["설계", "3월"]]},
    ], "감사합니다")


def _body_id(data: bytes, slide: int) -> str:
    return next(line.split(" ")[0].rstrip(":").split(",")[0]
                for line in pptx_edit.outline(data).splitlines()
                if line.startswith(f"s{slide}.") and "본문" in line)


def test_a_slide_as_a_table_is_a_real_table_in_place_of_its_text():
    data = _deck()
    body = _body_id(data, 2)
    edited = pptx_edit.apply(data, [{
        "op": "insert_table", "slide": "s2", "replace": body,
        "rows": [["항목", "값"], ["매출", "120억"], ["고객", "3만"]],
    }]).data
    slide = Presentation(io.BytesIO(edited)).slides[1]
    tables = [s for s in slide.shapes if s.has_table]
    assert len(tables) == 1 and tables[0].table.cell(1, 1).text == "120억"
    assert "매출 120억" not in pptx_edit.outline(edited).split("── s3")[0]


def test_table_columns():
    data = _deck()
    table = next(line.split(" ")[0] for line in pptx_edit.outline(data).splitlines()
                 if "(표 " in line)
    wider = pptx_edit.apply(data, [{"op": "insert_table_column", "table": table,
                                    "after": 0}]).data
    assert "r0: 단계 |  | 시기" in pptx_edit.outline(wider)
    back = pptx_edit.apply(wider, [{"op": "delete_table_column", "table": table,
                                    "col": 1}]).data
    assert "r0: 단계 | 시기" in pptx_edit.outline(back)


def test_pictures_and_layouts():
    data = _deck()
    pictures = {"a.png": ("image/png", _png()), "b.png": ("image/png", _png(100, 100))}
    edited = pptx_edit.apply(data, [{"op": "insert_picture", "slide": "s2", "file": "a.png"}],
                             pictures=pictures).data
    picture = next(line.split(":")[0] for line in pptx_edit.outline(edited).splitlines()
                   if "(그림)" in line)
    again = pptx_edit.apply(edited, [{"op": "replace_picture", "id": picture,
                                      "file": "b.png"}], pictures=pictures).data
    slide = Presentation(io.BytesIO(again)).slides[1]
    shape = next(s for s in slide.shapes if s.shape_type == 13)
    assert abs(shape.width - shape.height) < 2000
    text = pptx_edit.outline(data)
    assert "N번째 슬라이드" in text


def test_a_layout_change_keeps_what_the_slide_says():
    data = pptx_edit.blank_deck()
    data = pptx_edit.apply(data, [{"op": "replace_text", "id": "s1.1", "text": "제목입니다"}]).data
    edited = pptx_edit.apply(data, [{"op": "change_layout", "slide": "s1",
                                     "layout": "Title Only"}]).data
    assert "레이아웃: Title Only" in pptx_edit.outline(edited)
    assert "제목입니다" in pptx_edit.outline(edited)
