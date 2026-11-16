"""A report written on a Word form: the form's blank tables filled in place (its head row, its
합계 row, its unit), its sub-headings (1.1, 1.2) kept, empty cover fields marked 「[ ]」, the
cover's guidance gone."""

from __future__ import annotations

import io

from docx import Document

from app.services import docx_form, form_profile

SECTIONS = [
    {"heading": "사업 개요", "content": "### 추진 배경\n설비 기록이 흩어져 있다.\n\n"
                                      "### 사업 목표\n- 시범 고객 12곳"},
    {"heading": "추진 계획", "content": "| 단계 | 기간 | 주요 내용 | 산출물 |\n|---|---|---|---|\n"
                                      "| 1단계 | 2027.01 | 착수 | 계획서 |\n"
                                      "| 2단계 | 2027.03 | 시범 | 보고서 |\n"
                                      "| 3단계 | 2027.06 | 출시 | 제품 |"},
    {"heading": "소요 예산", "content": "| 항목 | 산출 근거 | 금액 |\n|---|---|---|\n"
                                      "| 개발비 | 인건비 | 1.8억 원 |\n| 합계 | | 1.8억 원 |"},
]


def _form() -> bytes:
    doc = Document()
    doc.add_paragraph("신사업 추진계획서", style="Title")
    cover = doc.add_table(rows=3, cols=2)
    for row, label in enumerate(("사업명", "책임자", "작성일")):
        cover.cell(row, 0).text = label
    doc.add_paragraph("※ 작성요령: 표지의 빈칸을 모두 채웁니다.")
    doc.add_heading("사업 개요", 1)
    doc.add_heading("추진 배경", 2)
    doc.add_paragraph("※ 작성요령: 배경을 적습니다.")
    doc.add_heading("사업 목표", 2)
    doc.add_heading("추진 계획", 1)
    plan = doc.add_table(rows=3, cols=4)
    for col, head in enumerate(("단계", "기간", "주요 내용", "산출물")):
        plan.cell(0, col).text = head
    doc.add_heading("소요 예산", 1)
    budget = doc.add_table(rows=3, cols=3)
    for col, head in enumerate(("항목", "금액(만원)", "산출 근거")):
        budget.cell(0, col).text = head
    budget.cell(2, 0).text = "합계"
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _written() -> tuple[Document, docx_form.Followed]:
    data = _form()
    profile = form_profile.profile("서식.docx", data)
    made, followed = docx_form.write(
        data, "별빛서치 신사업", SECTIONS, title_block={"fields": [["사업명", "별빛서치"]]},
        profile=profile, name="서식.docx")
    return Document(io.BytesIO(made)), followed


def test_the_forms_blank_tables_are_filled_in_place_in_their_unit():
    doc, followed = _written()
    plan, budget = doc.tables[1], doc.tables[2]
    assert [[c.text for c in r.cells] for r in plan.rows] == [
        ["단계", "기간", "주요 내용", "산출물"], ["1단계", "2027.01", "착수", "계획서"],
        ["2단계", "2027.03", "시범", "보고서"], ["3단계", "2027.06", "출시", "제품"]]
    # the report's own column order, the form's: 금액 sits where the form has it, in 만원
    assert [[c.text for c in r.cells] for r in budget.rows] == [
        ["항목", "금액(만원)", "산출 근거"], ["개발비", "18,000", "인건비"], ["합계", "18,000", ""]]
    assert len(followed.tables_filled) == 2
    # no second copy of the report's own table beside the form's
    assert len(doc.tables) == 3


def test_the_forms_subheadings_stay_over_the_content_of_their_name():
    doc, followed = _written()
    shown = [(p.style.name, p.text) for p in doc.paragraphs if p.text.strip()]
    at = shown.index(("Heading 2", "추진 배경"))
    assert shown[at + 1] == ("Body Text", "설비 기록이 흩어져 있다.")
    assert shown[at + 2] == ("Heading 2", "사업 목표")
    assert followed.subheadings == ["추진 배경", "사업 목표"]


def test_an_empty_cover_field_is_a_blank_and_the_cover_guidance_is_gone():
    doc, followed = _written()
    cover = [[c.text for c in r.cells] for r in doc.tables[0].rows]
    assert cover[0] == ["사업명", "별빛서치 신사업"]
    assert cover[1] == ["책임자", "[책임자]"] and cover[2] == ["작성일", "[작성일]"]
    assert followed.left_blank == ["책임자", "작성일"] and followed.filled == ["사업명"]
    assert not any("작성요령" in p.text for p in doc.paragraphs)


def test_an_amount_is_written_in_the_unit_of_its_column():
    assert docx_form.in_unit("1.8억 원", "만원") == "18,000"
    assert docx_form.in_unit("3,500만 원", "만원") == "3,500"
    assert docx_form.in_unit("12,000", "만원") == "12,000"
    assert docx_form.in_unit("1.8억 원", "") == "1.8억 원"
    assert docx_form.in_unit("착수", "만원") == "착수"


def test_a_sub_heading_the_writer_set_in_bold_text_still_goes_under_the_forms_heading():
    data = _form()
    profile = form_profile.profile("서식.docx", data)
    made, followed = docx_form.write(
        data, "신사업", [{"heading": "사업 개요", "content": "**추진 배경**\n흩어져 있다.\n\n"
                                                            "**사업 목표**\n12곳 시범"}],
        profile=profile, name="서식.docx")
    shown = [(p.style.name, p.text) for p in Document(io.BytesIO(made)).paragraphs
             if p.text.strip()]
    at = shown.index(("Heading 2", "추진 배경"))
    assert shown[at + 1][1] == "흩어져 있다." and shown[at + 2] == ("Heading 2", "사업 목표")
    assert followed.subheadings == ["추진 배경", "사업 목표"]
