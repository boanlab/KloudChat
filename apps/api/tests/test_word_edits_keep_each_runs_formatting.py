"""Word edit ops change text and keep each paragraph's style and numbering, each run's
character format and each cell's properties; text Word split across runs is still found; a
bad op changes nothing. Two real documents: a report from the export path, and a 공문 typed
in Word's way, with merged cells, a header, a footer and page-number fields."""

from __future__ import annotations

import io

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from app.services import docx_doc, docx_edit, report_export

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _font(run, name: str = "바탕", size: float = 11, bold: bool | None = None) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._r.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), name)


def _field(paragraph, instruction: str) -> None:
    for kind, text in (("begin", ""), ("instr", instruction), ("separate", ""),
                       ("text", "1"), ("end", "")):
        run = paragraph.add_run(text if kind == "text" else "")
        if kind == "instr":
            node = OxmlElement("w:instrText")
            node.set(qn("xml:space"), "preserve")
            node.text = text
            run._r.append(node)
        elif kind != "text":
            node = OxmlElement("w:fldChar")
            node.set(qn("w:fldCharType"), kind)
            run._r.append(node)


def gongmun() -> bytes:
    """A 공문 as people type one in Word: label runs in bold, a blank Word split over three
    runs (spell check does that), merged schedule cells with shading, a header, and a footer
    with PAGE / NUMPAGES fields."""
    doc = Document()
    section = doc.sections[0]
    head = section.header.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _font(head.add_run("[기관명] 정보전략팀"), size=9)
    foot = section.footer.paragraphs[0]
    _font(foot.add_run("- "), size=9)
    _field(foot, " PAGE ")
    _font(foot.add_run(" / "), size=9)
    _field(foot, " NUMPAGES ")
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(title.add_run("단 국 대 학 교"), "맑은 고딕", 20, True)
    to = doc.add_paragraph()
    _font(to.add_run("수신  "), "맑은 고딕", 11, True)
    for piece in ("[수", "신", "처]"):  # one blank, three runs
        _font(to.add_run(piece), "바탕", 11)
    subject = doc.add_paragraph()
    _font(subject.add_run("제목  "), "맑은 고딕", 11, True)
    _font(subject.add_run("[연수명] 참석 요청"), "바탕", 11)
    for line in ("1. 관련: [관련 근거]", "2. [연수명]을 아래와 같이 실시합니다.",
                 "3. 장소는 [장소]으로 합니다."):
        _font(doc.add_paragraph().add_run(line))
    listed = doc.add_paragraph(style="List Number")
    _font(listed.add_run("참석 대상: [참석 대상]"))
    table = doc.add_table(rows=4, cols=4)
    table.style = "Table Grid"
    table.cell(0, 2).merge(table.cell(0, 3))
    table.cell(1, 0).merge(table.cell(2, 0))
    rows = [["구분", "일정", "세부 내용", ""], ["1단계", "[일정]", "요구사항 수집", "면담"],
            ["", "2026. 12.", "시범 운영", "3개 부서"], ["비고", "", "", ""]]
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text:
                cell = table.cell(r, c)
                _font(cell.paragraphs[0].add_run(text), "맑은 고딕", 10, r == 0)
                if r == 0:
                    shade = OxmlElement("w:shd")
                    shade.set(qn("w:val"), "clear")
                    shade.set(qn("w:fill"), "D9E2F3")
                    cell._tc.get_or_add_tcPr().append(shade)
    _font(doc.add_paragraph().add_run("붙임  1. 연수 일정표 1부.  끝."))
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def report() -> bytes:
    sections = [
        {"heading": "추진 배경", "content": "문서 작성에 [소요 시간]이 걸린다.\n\n- 초안 작성\n"
                                          "- 서식 맞추기"},
        {"heading": "현황", "content": "| 구분 | 값 |\n|---|---|\n| 사용자 | [수치] |"},
    ]
    return report_export.to_docx("문서 작성 자동화 계획", sections)


def body(data: bytes):
    return Document(io.BytesIO(data))


def paragraph_with(data: bytes, start: str):
    for paragraph in body(data).paragraphs:
        if paragraph.text.startswith(start):
            return paragraph
    raise AssertionError(start)


def test_the_outline_numbers_paragraphs_marks_headings_and_lists_the_blanks():
    text = docx_edit.outline(report())
    assert "p2 (heading 1): 추진 배경" in text and "p4 (list): 초안 작성" in text
    assert "t1 (표 2×2)" in text and "  r1: 사용자 | [수치]" in text
    assert text.splitlines()[-1] == "빈칸: [소요 시간] (p3), [수치] (t1 r1c1)"

    text = docx_edit.outline(gongmun())
    # the blank Word split over three runs reads whole
    assert "p2: 수신  [수신처]" in text
    assert "(list): 참석 대상: [참석 대상]" in text
    # merged cells name their columns, and the lower half of a merge says so
    assert "t1 (표 4×4, 병합 셀 있음)" in text
    assert "  r0: c0: 구분 | c1: 일정 | c2~3: 세부 내용" in text
    assert "  r2: c0: (위 칸과 병합) | c1: 2026. 12." in text
    assert "머리글: [기관명] 정보전략팀" in text and "바닥글: - 1 / 1" in text
    blanks = text.splitlines()[-1]
    for blank in ("[수신처] (p2)", "[연수명] (p3, p5)", "[일정] (t1 r1c1)", "[기관명] (머리글)"):
        assert blank in blanks


def test_a_blank_split_across_runs_is_filled_in_the_format_it_started_in():
    data = gongmun()
    done = docx_edit.apply(data, [{"op": "fill_blank", "blank": "[수신처]", "value": "각 부서장"},
                                  {"op": "fill_blank", "blank": "[장소]", "value": "본관 강당"},
                                  {"op": "fill_blank", "blank": "[연수명]", "value": "교원연수"}])
    assert done.changes[0] == "[수신처] → 「각 부서장」 (1곳)"
    to = paragraph_with(done.data, "수신")
    assert to.text == "수신  각 부서장"
    # the label keeps its bold; the value keeps the run the blank began in
    assert [(r.text, r.bold) for r in to.runs if r.text][:2] == [("수신  ", True), ("각 부서장", None)]
    assert all(r.font.name == "바탕" for r in to.runs[1:])
    # the particle after a blank agrees with what fills it
    assert paragraph_with(done.data, "2.").text == "2. 교원연수를 아래와 같이 실시합니다."
    assert paragraph_with(done.data, "3.").text == "3. 장소는 본관 강당으로 합니다."
    assert paragraph_with(done.data, "제목").text == "제목  교원연수 참석 요청"


def test_replacing_a_line_keeps_its_style_numbering_and_first_runs_format():
    data = gongmun()
    done = docx_edit.apply(data, [
        {"op": "replace_text", "id": "p7", "text": "참석 대상: 전 직원"},
        {"op": "insert_paragraph", "after": "p7", "text": "참석 인원: [인원]"},
    ])
    listed = paragraph_with(done.data, "참석 대상")
    assert listed.style.name == "List Number"
    assert listed.runs[0].font.size == Pt(11) and listed.runs[0].font.name == "바탕"
    # the new paragraph takes the same list and run format
    added = paragraph_with(done.data, "참석 인원")
    assert added.style.name == "List Number" and added.runs[0].font.name == "바탕"
    assert "[인원]" in docx_edit.blanks(done.data)


def test_a_written_marker_keeps_its_tab_when_the_line_is_rewritten():
    data = docx_doc.official("연수 참석 요청", "1. 관련: [관련 근거]\n2. 참석 바랍니다.")
    outline = docx_edit.outline(data)
    line = next(x for x in outline.splitlines() if "관련" in x)
    pid = line.split(":")[0]
    done = docx_edit.apply(data, [{"op": "replace_text", "id": pid, "text": "1. 관련: 공문 제1호"}])
    assert "1.\t관련: 공문 제1호" in docx_edit.plain_text(done.data)


def test_cells_keep_their_properties_and_rows_copy_the_row_above():
    data = gongmun()
    done = docx_edit.apply(data, [
        {"op": "set_cell", "table": "t1", "row": 1, "col": 1, "text": "2026. 11."},
        {"op": "set_cell", "table": "t1", "row": 0, "col": 2, "text": "내용"},
    ])
    table = body(done.data).tables[0]
    assert table.cell(1, 1).text == "2026. 11."
    head = table.rows[0].cells[2]._tc
    assert head.find(f"{W}tcPr/{W}shd").get(f"{W}fill") == "D9E2F3"
    assert head.find(f"{W}tcPr/{W}gridSpan").get(f"{W}val") == "2"
    assert table.rows[0].cells[2].paragraphs[0].runs[0].bold

    plain = report()
    grown = docx_edit.apply(plain, [{"op": "insert_table_row", "table": "t1", "after": 1},
                                    {"op": "set_cell", "table": "t1", "row": 1, "col": 0,
                                     "text": "관리자"}])
    rows = [[c.text for c in row.cells] for row in body(grown.data).tables[0].rows]
    assert rows[1:] == [["관리자", "[수치]"], ["", ""]]


def test_the_header_footer_and_page_fields_survive_an_edit():
    data = gongmun()
    done = docx_edit.apply(data, [{"op": "fill_blank", "blank": "[기관명]", "value": "단국대학교"},
                                  {"op": "delete_paragraph", "id": "p4"}])
    section = body(done.data).sections[0]
    assert section.header.paragraphs[0].text == "단국대학교 정보전략팀"
    footer = section.footer._element
    instructions = [n.text for n in footer.iter(f"{W}instrText")]
    assert instructions == [" PAGE ", " NUMPAGES "]
    assert len(list(footer.iter(f"{W}fldChar"))) == 6
    assert "1. 관련" not in docx_edit.plain_text(done.data)


@pytest.mark.parametrize("ops, said", [
    ([{"op": "fill_blank", "blank": "[없는 칸]", "value": "x"}], "[없는 칸]이 없습니다"),
    ([{"op": "replace_text", "id": "p99", "text": "x"}], "'p99'이 없습니다"),
    ([{"op": "set_cell", "table": "t1", "row": 2, "col": 0, "text": "x"}], "r2c0 칸이 없습니다"),
    ([{"op": "insert_table_row", "table": "t1", "after": 0}], "세로로 병합된 칸"),
    ([{"op": "explode"}], "모르는 op"),
])
def test_one_bad_op_changes_nothing(ops, said):
    data = gongmun()
    good = {"op": "fill_blank", "blank": "[수신처]", "value": "각 부서장"}
    with pytest.raises(docx_edit.DocxError, match=said.replace("[", r"\[").replace("]", r"\]")):
        docx_edit.apply(data, [good, *ops])


def test_what_is_not_a_word_file_is_refused():
    for junk in (b"nope", b"PK\x03\x04broken", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy.doc"):
        assert docx_doc.file_format(junk) == ""
        with pytest.raises(docx_edit.DocxError):
            docx_edit.outline(junk)
    assert docx_doc.file_format(gongmun()) == "docx"
