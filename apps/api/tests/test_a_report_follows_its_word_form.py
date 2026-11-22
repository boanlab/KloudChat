"""A Word report written on a Word form keeps the form's cover, header, footer and styles, writes
each chapter under the form's heading, and is marked with what was followed."""

from __future__ import annotations

import io

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import RGBColor

from app.models.workspace import ArtifactKind
from app.pipeline import report_stages
from app.pipeline.stage import Context, Stop
from app.services import (
    docx_doc,
    docx_form,
    form_files,
    form_marks,
    form_profile,
    report,
    report_office,
)

SECTIONS = [
    {"heading": "사업 개요", "content": "설비 점검 기록을 한 곳에 모아 고장 징후를 미리 알린다.\n\n"
                                       "- 시범 고객 12곳\n- 점검 시간 30% 단축"},
    {"heading": "추진 계획", "content": "| 단계 | 기간 |\n|---|---|\n| 1단계 | 2027.03~06 |\n"
                                       "| 2단계 | 2027.07~[2단계 종료일] |"},
    {"heading": "예산", "content": "| 항목 | 금액(만원) |\n|---|---|\n| 개발 | 12,000 |"},
    {"heading": "위험 요인", "content": "부품 수급이 늦어질 수 있다."},
]


def _form(*, korean_styles: bool = False, headings: bool = True) -> bytes:
    """A company form: header and footer, a cover with label cells, then chapters each with
    its 작성요령. `korean_styles`: chapter headings in a style of the form's own name and id
    (as a Korean Word saves them), with no 「Heading 1」 at all."""
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "(주)한빛소프트 · 대외비"
    doc.sections[0].footer.paragraphs[0].text = "- 쪽 -"
    doc.add_paragraph("2027년 창업도약패키지 사업계획서", style="Title")
    cover = doc.add_table(rows=3, cols=2)
    for row, label in enumerate(("사업명", "기관명", "작성일")):
        cover.cell(row, 0).text = label
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    style = None
    if korean_styles:
        style = doc.styles.add_style("장 제목", WD_STYLE_TYPE.PARAGRAPH)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0x1F, 0x3A, 0x6B)
        outline = OxmlElement("w:outlineLvl")
        outline.set(qn("w:val"), "0")
        style.element.get_or_add_pPr().append(outline)
    for heading, guide in (("1. 사업 개요", "※ 작성요령: 사업의 배경과 목적을 적는다."),
                           ("2. 추진 계획", "※ 단계별 일정과 산출물을 적는다."),
                           ("3. 예산", "※ 항목별 금액(만원)을 표로 적는다.")):
        if not headings:
            doc.add_paragraph(heading)
        elif style is not None:
            doc.add_paragraph(heading, style=style)
        else:
            doc.add_heading(heading, 1)
        doc.add_paragraph(guide)
        doc.add_paragraph("")
    if korean_styles:
        # the shipped 「Heading 1」 gone, as in a form made from scratch in Korean Word
        doc.styles.element.remove(doc.styles["Heading 1"].element)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _paragraphs(data: bytes) -> list[tuple[str, str]]:
    return [(p.style.name, p.text) for p in Document(io.BytesIO(data)).paragraphs]


def test_each_chapter_goes_under_the_forms_heading_of_that_name():
    data, followed = docx_form.write(_form(), "설비 점검 통합 서비스", SECTIONS)
    lines = _paragraphs(data)
    texts = [text for _, text in lines]
    at = texts.index("1. 사업 개요")
    assert texts[at + 1] == "설비 점검 기록을 한 곳에 모아 고장 징후를 미리 알린다."
    assert followed.chapters == ["1. 사업 개요", "2. 추진 계획", "3. 예산"]
    # the form's guidance is gone, its headings are not repeated by the report's own
    assert not any("작성요령" in text for text in texts)
    assert texts.count("1. 사업 개요") == 1 and "사업 개요" not in texts
    assert followed.guidance_removed == 3


def test_a_chapter_the_form_lacks_is_added_in_its_heading_style_and_number():
    data, followed = docx_form.write(_form(), "설비 점검 통합 서비스", SECTIONS)
    assert followed.added == ["4. 위험 요인"]
    assert ("Heading 1", "4. 위험 요인") in _paragraphs(data)


def test_the_cover_header_and_footer_are_the_forms_and_its_cells_are_filled():
    block = {"fields": [["기관명", "(주)한빛소프트"], ["작성일", "2026. 11. 15."]]}
    data, followed = docx_form.write(_form(), "설비 점검 통합 서비스", SECTIONS, title_block=block)
    doc = Document(io.BytesIO(data))
    assert doc.paragraphs[0].text == "2027년 창업도약패키지 사업계획서"
    assert [[c.text for c in row.cells] for row in doc.tables[0].rows] == [
        ["사업명", "설비 점검 통합 서비스"], ["기관명", "(주)한빛소프트"],
        ["작성일", "2026. 11. 15."]]
    assert followed.filled == ["사업명", "기관명", "작성일"]
    assert doc.sections[0].header.paragraphs[0].text == "(주)한빛소프트 · 대외비"
    assert doc.sections[0].footer.paragraphs[0].text == "- 쪽 -"
    assert followed.header and followed.footer


def test_a_form_with_its_own_heading_style_and_no_heading_1_is_followed():
    data, followed = docx_form.write(_form(korean_styles=True), "서비스", SECTIONS)
    assert followed.chapters == ["1. 사업 개요", "2. 추진 계획", "3. 예산"]
    lines = _paragraphs(data)
    assert ("장 제목", "4. 위험 요인") in lines
    assert ("장 제목", "1. 사업 개요") in lines
    # the report's own headings took the form's style, not an added 「Heading 1」
    assert not any(style == "Heading 1" for style, _ in lines)


def test_a_form_with_no_chapter_headings_gets_the_report_after_its_content():
    data, followed = docx_form.write(_form(headings=False), "서비스", SECTIONS)
    texts = [text for _, text in _paragraphs(data)]
    assert texts.index("2027년 창업도약패키지 사업계획서") < texts.index(
        "설비 점검 기록을 한 곳에 모아 고장 징후를 미리 알린다.")
    assert not followed.chapters and len(followed.added) == 4


def test_what_is_not_a_word_form_is_refused():
    with pytest.raises(docx_form.FormWriteError):
        docx_form.write(b"not a zip", "x", SECTIONS)


# ── the conversation's form, the planner, the stored file ──────────────


def _resolved(data: bytes, source: str = "attached") -> form_files.Resolved:
    profile = form_profile.profile("사업계획서_서식.docx", data)
    return form_files.Resolved(
        form_files.FormChoice("docx", "f1", "사업계획서_서식.docx", source, profile), data)


def test_the_forms_chapters_are_the_outline_and_the_writer_is_told():
    form = _resolved(_form())
    assert form_files.outline(form) == ["사업 개요", "추진 계획", "예산"]
    note = form_files.writer_note(form)
    assert "사업 개요 / 추진 계획 / 예산" in note and "작성요령" in note


async def test_the_planner_keeps_the_forms_chapters(monkeypatch):
    async def complete(_model, _messages, _key, _max):
        return ('{"title": "사업계획서", "style": "편집형", "subject": "설비 점검", '
                '"sections": ["요약", "배경", "결론"], "alternatives": []}'), {
            "inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(report, "_complete", complete)
    bundle = report_stages.bundle_for(
        request="설비 점검 서비스 사업계획서를 써 줘.", model="m", api_key="k",
        untrusted_context=["설비 점검 기록을 모은다."], web_search=False,
        form_outline=["사업 개요", "추진 계획", "예산"])
    bundle.material.web_search = False
    with pytest.raises(Stop):
        async for _event in report_stages.Outline().run(bundle, Context(options={})):
            pass
    assert bundle.plan.headings == ["사업 개요", "추진 계획", "예산"]


def test_a_word_report_on_a_form_is_marked_and_described():
    data = {"sections": SECTIONS, "titleBlock": None}
    blob = report_office.file_bytes(ArtifactKind.docx, "설비 점검 통합 서비스", data,
                                    form=_resolved(_form(), "project"))
    mark = form_marks.read(blob)
    assert mark["kind"] == "docx" and mark["source"] == "project"
    assert mark["followed"]["chapters"] == ["1. 사업 개요", "2. 추진 계획", "3. 예산"]
    assert docx_doc.describe(blob, "docx")["form"] == mark
    # an HWP report takes no Word form
    hwp = report_office.file_bytes(ArtifactKind.hwp, "x", data, form=_resolved(_form()))
    assert form_marks.read(hwp) is None


def test_a_word_form_is_followed_now():
    row = form_profile.profile("신청서.docx", _form())
    assert row["verdict"] == "form" and "docx" in form_files.FOLLOWED
