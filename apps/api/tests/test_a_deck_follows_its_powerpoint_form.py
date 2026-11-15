"""A deck written on a PowerPoint form (양식): the form's layouts, placeholders, theme and slide
size, the form's sample slides gone, drawn content inside the layout's body area, and the
file marked with what was followed."""

from __future__ import annotations

import io

import pytest
from lxml import etree
from pptx import Presentation
from pptx.util import Emu

from app.models.chat import ChatSession, SessionKind
from app.models.workspace import StoredFile
from app.services import form_files, form_marks, form_profile, pptx_doc, pptx_form
from app.services.tools import pptx as pptx_tools

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"

SLIDES = [
    {"layout": "title", "title": "2027 사업계획 발표", "body": "한빛소프트 · 2026.11"},
    {"layout": "section", "title": "사업 개요"},
    {"layout": "bullets", "title": "추진 배경",
     "bullets": ["점검 기록이 종이와 엑셀에 흩어져 있다", "고장 징후를 놓쳐 라인이 멈췄다"]},
    {"layout": "two-column", "title": "지금과 바뀐 뒤",
     "columns": [["종이 점검표", "월말 집계"], ["태블릿 점검", "실시간 경보"]]},
    {"layout": "table", "title": "예산",
     "rows": [["항목", "금액(만원)"], ["개발", "12,000"], ["장비", "3,500"]]},
    {"layout": "chart", "title": "점검 시간",
     "chart": {"kind": "bar", "categories": ["1분기", "2분기", "3분기"],
               "series": [{"name": "시간", "values": [120, 95, 70]}]}},
    {"layout": "statement", "title": "점검 시간을 30% 줄인다", "body": "2027년 말까지"},
    {"layout": "closing", "title": "감사합니다", "body": "문의: [담당자]"},
]


def _company_form(*, wide: bool = True, accent: str = "C8102E") -> bytes:
    """A company form: 16:9, its own accent and fonts in the theme, a band on the master, and
    a sample slide still showing its prompts."""
    deck = Presentation()
    if wide:
        deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    master = deck.slide_masters[0]
    master.shapes._spTree.append(etree.fromstring(
        f'<p:sp xmlns:p="{_P}" xmlns:a="{_A}"><p:nvSpPr><p:cNvPr id="90" name="회사 띠"/>'
        '<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/>'
        f'<a:ext cx="{int(deck.slide_width)}" cy="200000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '<a:solidFill><a:schemeClr val="accent1"/></a:solidFill></p:spPr></p:sp>'))
    theme_part = next(rel.target_part for rel in master.part.rels.values()
                      if rel.reltype.endswith("/theme"))
    theme = etree.fromstring(theme_part.blob)
    accent1 = theme.find(f".//{{{_A}}}clrScheme/{{{_A}}}accent1/{{{_A}}}srgbClr")
    accent1.set("val", accent)
    for which in ("majorFont", "minorFont"):
        ea = theme.find(f".//{{{_A}}}{which}/{{{_A}}}ea")
        ea.set("typeface", "나눔고딕")
    theme_part._blob = etree.tostring(theme, xml_declaration=True, encoding="UTF-8",
                                      standalone=True)
    deck.slides.add_slide(deck.slide_layouts[0])
    deck.slides.add_slide(deck.slide_layouts[1])
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def _written(form: bytes, slides=SLIDES) -> tuple[Presentation, pptx_form.Followed]:
    data, followed = pptx_form.write(form, "2027 사업계획", slides, name="회사양식.pptx")
    return Presentation(io.BytesIO(data)), followed


def test_each_slide_takes_the_form_layout_its_kind_needs():
    deck, followed = _written(_company_form())
    names = [slide.slide_layout.name for slide in deck.slides]
    assert names == ["Title Slide", "Section Header", "Title and Content", "Two Content",
                     "Title Only", "Title Only", "Blank", "Title Slide"]
    assert followed.slides == 8 and not followed.stand_ins
    assert followed.layouts["Title Only"] == 2


def test_the_forms_own_sample_slides_are_gone_and_its_size_is_kept():
    form = _company_form()
    deck, followed = _written(form)
    assert len(deck.slides) == len(SLIDES)
    assert (deck.slide_width, deck.slide_height) == (12192000, 6858000)
    assert followed.size == "16:9"
    narrow, _ = _written(_company_form(wide=False))
    assert (narrow.slide_width, narrow.slide_height) == (9144000, 6858000)


def test_words_go_into_the_forms_placeholders():
    deck, _ = _written(_company_form())
    cover, _section, bullets, two = list(deck.slides)[:4]
    assert cover.shapes.title.text == "2027 사업계획 발표"
    assert [p.text_frame.text for p in cover.placeholders][1] == "한빛소프트 · 2026.11"
    body = [p for p in bullets.placeholders if p.placeholder_format.idx == 1][0]
    assert [p.text for p in body.text_frame.paragraphs] == SLIDES[2]["bullets"]
    columns = sorted((p for p in two.placeholders if p.placeholder_format.idx in (1, 2)),
                     key=lambda p: p.left)
    assert [c.text_frame.text for c in columns] == [
        "종이 점검표\n월말 집계", "태블릿 점검\n실시간 경보"]


def test_drawn_content_sits_in_the_body_area_in_theme_colours_and_type():
    deck, _ = _written(_company_form())
    table_slide, chart_slide = list(deck.slides)[4:6]
    title = table_slide.shapes.title
    table = next(s for s in table_slide.shapes if s.has_table)
    assert table.top >= title.top + title.height - Emu(10000)
    assert table.left >= 0 and table.left + table.width <= deck.slide_width
    assert any(s.has_chart for s in chart_slide.shapes)
    # nothing moved is still a placeholder (the form's layout would move it back)
    for slide in (table_slide, chart_slide):
        title_id = slide.shapes.title.shape_id
        for shape in slide.shapes:
            if shape.shape_id != title_id:
                assert not shape.is_placeholder
    xml = etree.tostring(table._element).decode()
    assert 'schemeClr val="accent1"' in xml
    assert "<a:latin" not in xml and "<a:ea " not in xml


def test_a_statement_is_drawn_whole_on_the_blank_layout():
    deck, _ = _written(_company_form())
    statement = list(deck.slides)[6]
    texts = [s.text_frame.text for s in statement.shapes if s.has_text_frame]
    assert "점검 시간을 30% 줄인다" in texts and not list(statement.placeholders)


def test_a_form_without_a_layout_for_a_kind_uses_a_stand_in():
    deck = Presentation()
    keep = {"Title Slide", "Title and Content"}
    layouts = deck.slide_masters[0].slide_layouts
    for layout in list(layouts):
        if layout.name not in keep:
            layouts.remove(layout)
    out = io.BytesIO()
    deck.save(out)
    written, followed = _written(out.getvalue())
    assert {s.slide_layout.name for s in written.slides} <= keep
    kinds = {kind for _, kind, _ in followed.stand_ins}
    assert {"section", "two", "drawn", "free"} <= kinds


def test_a_template_file_is_written_on_too():
    form = _company_form()
    from test_a_form_file_is_told_from_material import _as_template

    deck, _ = _written(_as_template(form, "pptx"))
    assert len(deck.slides) == len(SLIDES)


def test_what_is_not_a_powerpoint_form_is_refused():
    with pytest.raises(pptx_form.FormWriteError):
        pptx_form.write(b"not a zip", "x", SLIDES)


# ── the mark, the document, the tools ──────────────────────────────────


def _resolved(form: bytes, source: str = "project") -> form_files.Resolved:
    profile = form_profile.profile("회사양식.pptx", form)
    return form_files.Resolved(
        form_files.FormChoice("pptx", "f1", "회사양식.pptx", source, profile), form)


def test_the_file_says_which_form_it_followed():
    data = pptx_doc.on_form(_resolved(_company_form()), "제목", SLIDES)
    mark = form_marks.read(data)
    assert mark["name"] == "회사양식.pptx" and mark["source"] == "project"
    assert mark["followed"]["slides"] == len(SLIDES)
    assert pptx_doc.describe(data, "pptx")["form"] == mark
    Presentation(io.BytesIO(data))
    assert form_marks.read(_company_form()) is None


def test_a_blank_deck_on_a_form_is_its_cover_only():
    data = pptx_doc.blank(form=_resolved(_company_form()))
    deck = Presentation(io.BytesIO(data))
    assert len(deck.slides) == 1 and deck.slides[0].slide_layout.name == "Title Slide"
    assert pptx_doc.describe(data, "pptx")["text"].strip() == ""


def test_the_powerpoint_write_tool_follows_the_conversations_form():
    form = _resolved(_company_form())
    title, data = pptx_tools._build(
        {"title": "보고", "slides": [{"title": "현황", "bullets": ["하나", "둘"]},
                                    {"title": "예산", "rows": [["항목", "금액"], ["개발", "1"]]}]},
        (None, "", form))
    deck = Presentation(io.BytesIO(data))
    assert [s.slide_layout.name for s in deck.slides] == [
        "Title Slide", "Title and Content", "Title Only"]
    assert form_marks.read(data)["name"] == "회사양식.pptx"


# ── the conversation keeps its form ────────────────────────────────────


def _file(name: str, data: bytes) -> StoredFile:
    row = StoredFile(user_id="u", name=name, session_id="s")
    row.form_profile = form_profile.profile(name, data)
    return row


def test_an_attached_form_is_kept_for_later_turns_and_taken_off_on_request():
    session = ChatSession(id="s", user_id="u", kind=SessionKind.slides)
    form = _file("발표양식.pptx", _company_form())
    turn = form_files.choose([form], None, "발표 자료 만들어 줘")
    form_files.remember(session, turn, None)
    assert session.form_files == {"pptx": form.id}
    assert form_files.attached_ids(session) == {form.id}
    # a later turn's off-switch only covers saved forms; the attached one stays
    form_files.remember(session, form_files.TurnForms(), ["pptx"])
    assert session.form_files == {"pptx": form.id}
    other = ChatSession(id="t", user_id="u", kind=SessionKind.slides)
    form_files.remember(other, form_files.TurnForms(), ["pptx"])
    assert other.form_files == {"pptx": ""}
    form_files.remember(other, form_files.TurnForms(), [])
    assert other.form_files is None


def test_the_question_lists_its_form_and_the_hwp_note():
    form = _file("발표양식.pptx", _company_form())
    hwp = StoredFile(user_id="u", name="신청서.hwp")
    hwp.form_profile = form_profile.profile("신청서.hwp", b"", "성명 ○○○ 기관 ○○○ ※ 작성요령")
    turn = form_files.choose([form, hwp], {hwp.id: "form"}, "만들어 줘")
    meta = form_files.turn_meta(turn, [{"id": form.id, "name": form.name},
                                       {"id": hwp.id, "name": hwp.name}])
    assert meta[0]["role"] == "form" and meta[0]["formKind"] == "pptx"
    assert meta[1]["role"] == "material" and "(*.hwpx)" in meta[1]["formNote"]
    saved = _file("프로젝트양식.pptx", _company_form())
    turn = form_files.choose([], None, "만들어 줘", project_forms={"pptx": saved})
    meta = form_files.turn_meta(turn, None)
    assert meta == [{"id": saved.id, "name": saved.name, "role": "form", "formKind": "pptx",
                     "source": "project"}]


def _named_form() -> bytes:
    """The company form with some layouts named for their use, as corporate forms are."""
    deck = Presentation(io.BytesIO(_company_form()))
    layouts = deck.slide_layouts
    layouts[2].name = "마무리"
    layouts[3].name = "핵심 숫자"
    layouts[5].name = "표 전용"
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def test_a_layout_named_for_its_use_is_taken_before_the_generic_one():
    slides = [
        {"layout": "title", "title": "3분기 실적", "body": ""},
        {"layout": "bullets", "title": "핵심", "bullets": ["매출 1,820", "이익 412", "고객 52사"]},
        {"layout": "table", "title": "분기별",
         "rows": [["분기", "매출"], ["1분기", "1,820"]]},
        {"layout": "bullets", "title": "배경", "bullets": ["점검 기록이 흩어져 있다"]},
        {"layout": "closing", "title": "[감사 인사 또는 Q&A 안내]", "body": ""},
    ]
    deck, followed = _written(_named_form(), slides)
    names = [slide.slide_layout.name for slide in deck.slides]
    assert names == ["Title Slide", "핵심 숫자", "표 전용", "Title and Content", "마무리"]
    assert not followed.stand_ins
    last = list(deck.slides)[-1]
    texts = [shape.text_frame.text for shape in last.shapes if shape.has_text_frame]
    assert "감사합니다" in texts
    assert not any("[" in text for text in texts)


def test_a_shape_past_the_slide_edge_is_brought_inside_with_its_table():
    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(12192000), Emu(6858000)
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    table = slide.shapes.add_table(4, 3, Emu(3000000), Emu(1500000), Emu(12000000), Emu(2000000))
    pptx_form.clamp(slide, (12192000, 6858000))
    assert table.left >= 0 and table.left + table.width <= 12192000
    assert table.top + table.height <= 6858000
    assert sum(column.width for column in table.table.columns) == table.width
