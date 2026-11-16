"""A PowerPoint written for a request holds what was asked and what the reply says it holds:
a native chart built from the given numbers (a missing value a gap, not 0), the slide count
asked for, tables that fit the slide, a paper's own figures cut from its PDF, and a reply
held to what the saved file contains."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from pptx import Presentation

from app.services import deck_export, office_claims, pdf_figures, pdf_text, pptx_edit
from app.services.tools import pptx as tool

SALES = {
    "title": "분기별 제품 매출",
    "chart": {"kind": "bar", "unit": "백만 원", "categories": ["1분기", "2분기", "3분기"],
              "series": [{"name": "StarVault", "values": [1820, 1950, 2080]},
                         {"name": "컨설팅", "values": [610, 580, None]}]},
    "bullets": ["3분기 컨설팅은 집계 중"],
}


def _deck(slides, **more):
    return tool._build({"title": "2026년 분기 실적", "slides": slides, **more})[1]


def test_a_chart_slide_is_a_native_chart_with_a_gap_for_a_missing_value():
    data = _deck([SALES])
    assert pptx_edit.contents(data) == {"slides": 2, "chart": 1, "table": 0, "picture": 0}
    chart = next(shape.chart for shape in Presentation(io.BytesIO(data)).slides[1].shapes
                 if getattr(shape, "has_chart", False) and shape.has_chart)
    consulting = list(chart.plots[0].series)[1]
    assert list(consulting.values)[:2] == [610, 580]
    # the third quarter is not drawn as 0
    assert list(consulting.values)[2] is None
    assert "3분기 컨설팅은 집계 중" in pptx_edit.plain_text(data)


def test_slides_sent_as_a_json_string_are_read():
    import json

    assert pptx_edit.contents(tool._build(
        {"title": "t", "slides": json.dumps([{"title": "a", "bullets": ["b"]}])})[1])["slides"] == 2


def test_a_chart_with_no_numbers_is_refused():
    with pytest.raises(ValueError, match="수가 없습니다"):
        _deck([{"title": "x", "chart": {"categories": ["a", "b"],
                                        "series": [{"name": "s", "values": ["집계 중", None]}]}}])


def test_the_slide_count_asked_counts_the_whole_file():
    eight = {"title": "t", "slides": [{"title": str(i)} for i in range(7)]}
    assert tool.count_problem("분기 실적 PowerPoint 8장 만들어 줘", eight) == ""
    nine = {**eight, "closing": "감사합니다"}
    problem = tool.count_problem("분기 실적 PowerPoint 8장 만들어 줘", nine)
    assert "9장" in problem and "slides를 6개" in problem
    assert tool.count_problem("10~12장으로 만들어 줘", eight) != ""
    assert tool.count_problem("발표 자료 만들어 줘", eight) == ""


def test_a_table_too_tall_for_the_slide_is_refused_and_a_short_one_fits():
    long_cell = "요청별로 최대 길이만큼 미리 연속 메모리를 잡아 두는 방식이라 내부 단편화가 크다"
    rows = [["항목", "A", "B", "C"]] + [[f"행 {i}", long_cell, long_cell + " (B)",
                                        long_cell + " (C)"] for i in range(7)]
    with pytest.raises(ValueError, match="넘칩니다"):
        _deck([{"title": "비교", "rows": rows}])
    width, height = deck_export.table_room()
    size, heights, fits = deck_export.table_fit(rows[:3], width, height)
    assert fits and sum(heights) <= height and size >= 12


def _reply_note(reply: str, contents: dict) -> str:
    ctx = SimpleNamespace(pptx={"saved": [{"version": 2, "changes": ["작성"],
                                           "contents": contents}]})
    return office_claims.note(reply, ctx, {"pptx"})


def test_a_reply_claiming_a_chart_the_file_lacks_is_corrected():
    plain = {"slides": 8, "chart": 0, "table": 1, "picture": 0}
    note = _reply_note("8장을 만들었습니다. 분기별 제품 매출 차트를 넣었습니다.", plain)
    assert "차트가 없습니다" in note
    assert _reply_note("8장을 만들었습니다. 분기별 제품 매출 차트를 넣었습니다.",
                       {**plain, "chart": 1}) == ""
    # saying it could not is no claim
    assert "차트" not in _reply_note("8장을 만들었습니다. 차트는 넣지 못했습니다.", plain)


def test_a_reply_giving_the_wrong_slide_count_is_corrected():
    note = _reply_note("12장짜리 발표 자료를 만들었습니다.",
                       {"slides": 11, "chart": 0, "table": 0, "picture": 0})
    assert "11장" in note


def _paper_pdf() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=letter)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, 740, "1 Introduction")
    pdf.drawString(72, 726, "Serving systems waste memory on reserved slots.")
    # the figure: bars drawn as paths, then its caption under it
    for i, height in enumerate((60, 120, 90)):
        pdf.rect(100 + i * 60, 520, 40, height, stroke=1, fill=1)
    pdf.line(90, 518, 300, 518)
    pdf.drawString(72, 500, "Figure 2. Memory waste in serving systems.")
    pdf.drawString(72, 470, "The text goes on under the figure.")
    pdf.showPage()
    pdf.save()
    return out.getvalue()


def test_a_figure_is_found_by_its_caption_and_cut_out():
    data = _paper_pdf()
    found = pdf_figures.find(data)
    assert [(f.label, f.page) for f in found] == [("Figure 2", 1)]
    figure = found[0]
    assert figure.caption.startswith("Figure 2. Memory waste")
    left, bottom, right, top = figure.box
    # the bars, not the body text above or the caption below
    assert 500 <= bottom <= 520 and 640 <= top < 726 and left < 100 and right > 260
    png = pdf_figures.cut(data, figure)
    assert png.startswith(b"\x89PNG")


def test_a_figure_picture_goes_on_its_slide_with_the_caption():
    png = pdf_figures.cut(_paper_pdf(), pdf_figures.find(_paper_pdf())[0])
    data = _deck([{"title": "메모리 낭비", "image": {"data": png,
                                                  "caption": "Figure 2 (Kwon et al., 2023)"}}])
    assert pptx_edit.contents(data)["picture"] == 1


def test_pdf_text_keeps_the_text_layer_and_tidies_pdfium_marks():
    assert pdf_text._tidy("How￾\r\never it is .") == "However it is."
    pages = pdf_text.page_texts(_paper_pdf())
    assert "Serving systems waste memory on reserved slots." in pages[0]


def test_a_count_with_its_parts_in_brackets_is_the_deck_size():
    contents = {"slides": 15, "chart": 0, "table": 0, "picture": 4}
    reply = "PowerPoint 15장(표지 + 본문 13장 + 토론 질문)을 만들었습니다. 그림 4개를 넣었습니다."
    assert _reply_note(reply, contents) == ""


async def test_the_count_is_read_from_the_opening_when_material_follows():
    ctx = SimpleNamespace(
        request="vLLM 논문 발표할 PowerPoint 12장 만들어 줘.\n\n# 자료\n본문 3장 …")
    with pytest.raises(ValueError, match="12장"):
        await tool._prepare({"title": "t", "slides": [{"title": str(i)} for i in range(14)]}, ctx)


def test_a_wide_figure_takes_the_width_and_keeps_its_lines_in_a_strip_under_it():
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (1200, 400), "white").save(out, format="PNG")
    data = _deck([{"title": "성능", "bullets": ["처리량 2~4배"],
                   "image": {"data": out.getvalue(), "caption": "Figure 12"}}])
    slide = Presentation(io.BytesIO(data)).slides[1]
    # the result stays on the slide, under the picture, which keeps the slide's width
    picture = next(s for s in slide.shapes if s.shape_type == 13)
    line = next(s for s in slide.shapes
                if s.has_text_frame and "처리량 2~4배" in s.text_frame.text)
    assert picture.width > Presentation(io.BytesIO(data)).slide_width * 0.6
    assert line.top >= picture.top + picture.height - 1


async def test_chart_values_sent_as_strings_are_numbers_and_words_are_gaps():
    ctx = SimpleNamespace(request="분기 실적 PowerPoint 2장 만들어 줘")
    slide = {"title": "매출", "chart": {"categories": ["1분기", "2분기", "3분기"], "series": [
        {"name": "컨설팅", "values": ["610", "580", "집계 중"]},
        {"name": "StarVault", "values": ["1,820", "1,950백만", 2080]}]}}
    args = await tool._prepare({"title": "t", "slides": [slide]}, ctx)
    values = [item["values"] for item in args["slides"][0]["chart"]["series"]]
    assert values == [[610.0, 580.0, None], [1820.0, 1950.0, 2080.0]]
    assert pptx_edit.contents(tool._build(args)[1])["chart"] == 1


async def test_a_picture_that_is_not_there_is_left_out_and_said(monkeypatch):
    from app.services.tools import office

    async def none_found(db, ctx, ops):
        return {}

    monkeypatch.setattr(office, "_pictures", none_found)
    ctx = SimpleNamespace(request="발표 자료 2장", user_id="u", session_id="s")
    args = await tool._prepare({"title": "t", "slides": [
        {"title": "그림", "bullets": ["설명"], "image": {"file": "2309.06180-Figure8.png"}}]}, ctx)
    assert "image" not in args["slides"][0]
    assert "Figure8" in args[tool.NOTES][0]


async def test_the_count_comes_from_the_persons_own_words():
    ctx = SimpleNamespace(asked="vLLM 논문 발표할 PowerPoint 12장 만들어 줘.",
                          request="# 프로젝트 지식\n… 3장 …\n\n질문")
    with pytest.raises(ValueError, match="12장"):
        await tool._prepare({"title": "t", "slides": [{"title": str(i)} for i in range(14)]}, ctx)


async def test_a_wrong_count_is_refused_once_then_saved_with_a_note():
    ctx = SimpleNamespace(asked="PowerPoint 12장 만들어 줘", pptx={})
    args = {"title": "t", "slides": [{"title": str(i)} for i in range(14)]}
    with pytest.raises(ValueError, match="12장"):
        await tool._prepare(args, ctx)
    again = await tool._prepare(args, ctx)
    assert "15장" in again[tool.NOTES][0]
    # one slide off is said, not sent back
    near = await tool._prepare({"title": "t", "slides": [{"title": str(i)} for i in range(12)]},
                               SimpleNamespace(asked="PowerPoint 12장 만들어 줘", pptx={}))
    assert "13장" in near[tool.NOTES][0]


async def test_unreadable_slides_say_so_before_any_count():
    ctx = SimpleNamespace(asked="PowerPoint 12장 만들어 줘", pptx={})
    with pytest.raises(ValueError, match="배열"):
        await tool._prepare({"title": "t", "slides": "[{broken"}, ctx)


async def test_text_past_its_place_is_saved_with_a_note():
    long = ("PagedAttention은 KV 캐시를 고정 크기 블록으로 나누고 블록 테이블로 논리 블록과 "
            "물리 블록을 잇는다")
    ctx = SimpleNamespace(asked="발표 자료 2장", pptx={})
    args = {"title": "t", "slides": [{"title": "넘치는 장", "bullets": [long] * 14}]}
    saved = await tool._prepare(args, ctx)
    assert any("2장의 글이" in note for note in saved[tool.NOTES])
    fine = {"title": "t", "slides": [{"title": "알맞은 장", "bullets": ["짧은 줄", "또 한 줄"]}]}
    assert (await tool._prepare(fine, SimpleNamespace(asked="발표 자료 2장", pptx={})))[
        tool.NOTES] == []
