"""PowerPoint edit ops change text and keep each paragraph's level and bullets, each run's
character format and each cell's fill; the visible title and the hidden outline title of
KloudChat's own decks move together; slide ops copy, add, move and delete slides; a bad op
changes nothing. Two real decks: one from the export path, and a company deck on its layouts
with a bold label run, a table, a chart and notes."""

from __future__ import annotations

import io

import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.services import deck_export, pptx_doc, pptx_edit


def company() -> bytes:
    """A deck as people make one on a template: a title slide, a bulleted slide with a bold
    「담당:」 label run before a blank, a table with a filled header row, a chart, notes."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = "2027년 [사업명] 추진 계획"
    cover.placeholders[1].text = "[부서명] · [YYYY. MM. DD.]"
    cover.notes_slide.notes_text_frame.text = "인사 후 목적부터."
    body_slide = prs.slides.add_slide(prs.slide_layouts[1])
    body_slide.shapes.title.text = "추진 배경"
    frame = body_slide.placeholders[1].text_frame
    frame.text = "업무 처리 시간이 부서별로 최대 3배 차이"
    second = frame.add_paragraph()
    second.level = 1
    label = second.add_run()
    label.text = "담당: "
    label.font.bold = True
    label.font.size = Pt(20)
    value = second.add_run()
    value.text = "[담당자]을 정한다"
    value.font.size = Pt(20)
    value.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    third = frame.add_paragraph()
    third.text = "문서 검색에 하루 47분 소요"
    body_slide.notes_slide.notes_text_frame.text = "수치 출처: [보고서명]."
    table_slide = prs.slides.add_slide(prs.slide_layouts[5])
    table_slide.shapes.title.text = "단계별 일정"
    table = table_slide.shapes.add_table(3, 3, Inches(1), Inches(1.5), Inches(8), Inches(2)).table
    for c, text in enumerate(("단계", "기간", "담당")):
        table.cell(0, c).text = text
    for c, text in enumerate(("1단계", "[기간]", "기획팀")):
        table.cell(1, c).text = text
    table.cell(2, 0).text = "2단계"
    table.cell(1, 2).fill.solid()
    table.cell(1, 2).fill.fore_color.rgb = RGBColor(0xEE, 0xF2, 0xFF)
    chart_slide = prs.slides.add_slide(prs.slide_layouts[5])
    chart_slide.shapes.title.text = "분기별 예산"
    data = CategoryChartData()
    data.categories = ["1분기", "2분기"]
    data.add_series("인프라", (12, 18))
    chart_slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1.5), Inches(8), Inches(4), data
    )
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def house() -> bytes:
    """A deck from KloudChat's own export: free text boxes, the title also in a 1 pt hidden
    placeholder, written 「• 」 bullet runs in the accent colour."""
    return deck_export.to_pptx("사업 점검", [
        {"layout": "title", "title": "2026년 하반기 사업 점검", "body": "[발표자]"},
        {"layout": "bullets", "title": "핵심 요약",
         "bullets": ["사용자 31% 증가", "응답 [지연] 단축"],
         "notes": "숫자는 9월 말 기준."},
    ])


def _prs(data: bytes):
    return Presentation(io.BytesIO(data))


def _runs(paragraph) -> list[tuple[str, bool | None, object]]:
    return [(r.text, r.font.bold, r.font.size) for r in paragraph.runs]


def test_the_outline_names_slides_shapes_tables_charts_notes_and_blanks():
    text = pptx_edit.outline(company())
    assert "슬라이드 4장" in text
    assert "── s2 (레이아웃: Title and Content) ──" in text
    assert "s1.1 (제목): 2027년 [사업명] 추진 계획" in text
    assert "s1.2 (부제목): [부서명] · [YYYY. MM. DD.]" in text
    assert ("s2.2 (본문), 3줄:\n    업무 처리 시간이 부서별로 최대 3배 차이\n"
            "    담당: [담당자]을 정한다") in text
    assert "s3.2 (표 3×3)\n  r0: 단계 | 기간 | 담당\n  r1: 1단계 | [기간] | 기획팀" in text
    assert "s4.2: (차트, 계열 1개; 고칠 수 없음)" in text
    assert "s2 메모: 수치 출처: [보고서명]." in text
    assert "[담당자] (s2.2)" in text and "[기간] (s3.2 r1c1)" in text
    assert "[보고서명] (s2 메모)" in text


def test_the_hidden_outline_title_is_not_listed_and_follows_the_visible_one():
    data = house()
    text = pptx_edit.outline(data)
    # one title per slide in the outline, the visible box, named as the title
    assert text.count("2026년 하반기 사업 점검") == 1
    assert "s1.1 (제목), 2줄:" in text and "s2.1 (제목): 핵심 요약" in text
    edited = pptx_edit.apply(data, [
        {"op": "replace_text", "id": "s1.1", "text": "2026년 사업 점검 결과\n[발표자]"},
    ]).data
    cover = _prs(edited).slides[0]
    titles = [s.text_frame.text for s in cover.shapes if s.has_text_frame and s.text_frame.text]
    # the visible box and its hidden twin
    assert titles == ["2026년 사업 점검 결과\n[발표자]"] * 2
    assert cover.shapes.title.text_frame.text == "2026년 사업 점검 결과\n[발표자]"


def test_a_rewrite_keeps_lines_that_stay_and_the_runs_of_what_it_shares():
    data = company()
    result = pptx_edit.apply(data, [{
        "op": "replace_text", "id": "s2.2",
        "text": "업무 처리 시간이 부서별로 최대 3배 차이\n담당: 김민수 팀장을 정한다\n"
                "결재 단계 평균 4.2회\n문서 검색에 하루 47분 소요",
    }])
    paragraphs = _prs(result.data).slides[1].placeholders[1].text_frame.paragraphs
    assert [p.text for p in paragraphs] == [
        "업무 처리 시간이 부서별로 최대 3배 차이", "담당: 김민수 팀장을 정한다",
        "결재 단계 평균 4.2회", "문서 검색에 하루 47분 소요",
    ]
    # the bold label run stays bold; the changed middle took the value run's format
    assert _runs(paragraphs[1]) == [
        ("담당: ", True, Pt(20)), ("김민수 팀장을 정한다", None, Pt(20)),
    ]
    assert paragraphs[1].runs[1].font.color.rgb == RGBColor(0x1F, 0x4E, 0x79)
    # a new line copies the paragraph before it: its level too
    assert paragraphs[1].level == 1 and paragraphs[2].level == 1
    assert "s2.2" in result.changes[0]


def test_a_blank_is_filled_with_an_agreeing_particle_in_the_run_it_started_in():
    result = pptx_edit.apply(company(), [
        {"op": "fill_blank", "blank": "[담당자]", "value": "기획팀"},
        {"op": "fill_blank", "blank": "[기간]", "value": "2027. 1.~3.", "at": "s3.2 r1c1"},
        {"op": "fill_blank", "blank": "[보고서명]", "value": "3분기 감사 보고서", "at": "s2 메모"},
    ])
    prs = _prs(result.data)
    paragraph = prs.slides[1].placeholders[1].text_frame.paragraphs[1]
    assert _runs(paragraph) == [("담당: ", True, Pt(20)), ("기획팀을 정한다", None, Pt(20))]
    assert prs.slides[2].shapes[1].table.cell(1, 1).text == "2027. 1.~3."
    assert prs.slides[1].notes_slide.notes_text_frame.text == "수치 출처: 3분기 감사 보고서."
    assert pptx_edit.blanks(result.data) == ["[사업명]", "[부서명]", "[YYYY. MM. DD.]"]
    assert result.changes[0] == "[담당자] → 「기획팀」 (1곳)"


def test_cells_keep_their_fill_and_rows_copy_the_row_above():
    result = pptx_edit.apply(company(), [
        {"op": "set_cell", "table": "s3.2", "row": 1, "col": 2, "text": "정보전략팀"},
        {"op": "insert_table_row", "table": "s3.2", "after": 1},
        {"op": "delete_table_row", "table": "s3.2", "row": 2},
    ])
    frame = _prs(result.data).slides[2].shapes[1]
    table = frame.table
    assert len(table.rows) == 3
    assert [table.cell(r, 0).text for r in range(3)] == ["단계", "1단계", ""]
    assert table.cell(1, 2).text == "정보전략팀"
    assert table.cell(1, 2).fill.fore_color.rgb == RGBColor(0xEE, 0xF2, 0xFF)
    # the new row copied 1단계's row, so its third cell keeps that fill
    assert table.cell(2, 2).fill.fore_color.rgb == RGBColor(0xEE, 0xF2, 0xFF)


def test_notes_are_set_and_a_slide_without_notes_gets_them():
    result = pptx_edit.apply(company(), [
        {"op": "set_notes", "slide": "s4", "text": "예산은 [금액] 기준.\n둘째 줄"},
    ])
    notes = _prs(result.data).slides[3].notes_slide.notes_text_frame.text
    assert notes == "예산은 [금액] 기준.\n둘째 줄"


def test_slides_are_added_on_the_layout_copied_moved_and_deleted_in_one_call():
    data = company()
    result = pptx_edit.apply(data, [
        {"op": "add_slide", "after": "s2", "title": "추진 목표",
         "body": "처리 시간 [목표치] 단축\n결재 2단계로"},
        {"op": "duplicate_slide", "id": "s4"},
        {"op": "move_slide", "id": "s3", "after": "s0"},
        {"op": "delete_slide", "id": "s1"},
    ])
    prs = _prs(result.data)
    titles = [s.shapes.title.text if s.shapes.title is not None else "" for s in prs.slides]
    assert titles == ["단계별 일정", "추진 배경", "추진 목표", "분기별 예산", "분기별 예산"]
    added = prs.slides[2]
    # a real template: the new slide is its layout's, title and body placeholders filled
    assert added.slide_layout.name == "Title and Content"
    assert [p.text for p in added.placeholders[1].text_frame.paragraphs] == [
        "처리 시간 [목표치] 단축", "결재 2단계로",
    ]
    # each chart is its own part, so changing one in PowerPoint leaves the other
    charts = {s.shapes[1].chart.part.partname for s in list(prs.slides)[3:]}
    assert len(charts) == 2
    assert "번호" not in " ".join(result.changes)
    assert result.changes[-2:] == ["s3를 맨 앞으로 옮김", "s1 삭제"]


def test_a_slide_added_to_a_house_deck_copies_the_look_and_keeps_its_bullets():
    data = house()
    result = pptx_edit.apply(data, [
        {"op": "add_slide", "after": "s2", "title": "다음 단계",
         "body": ["점검 [일정] 확정", "보고"]},
    ])
    text = pptx_edit.outline(result.data)
    assert "s3.1 (제목): 다음 단계" in text
    assert "s3.2 (본문), 2줄:\n    • 점검 [일정] 확정\n    • 보고" in text
    slide = _prs(result.data).slides[2]
    body = next(s for s in slide.shapes if s.has_text_frame and "보고" in s.text_frame.text)
    bullet, words = body.text_frame.paragraphs[0].runs
    # the written bullet keeps the accent colour, the words the ink
    assert bullet.text == "• " and bullet.font.color.rgb != words.font.color.rgb
    # the copied slide's notes are not the old slide's
    assert not slide.has_notes_slide or not slide.notes_slide.notes_text_frame.text


@pytest.mark.parametrize(("ops", "said"), [
    ([{"op": "replace_text", "id": "s9.1", "text": "x"}], "s9.1"),
    ([{"op": "replace_text", "id": "s3.2", "text": "x"}], "set_cell"),
    ([{"op": "replace_text", "id": "s4.2", "text": "x"}], "차트"),
    ([{"op": "fill_blank", "blank": "[없는 칸]", "value": "x"}], "없습니다"),
    ([{"op": "set_cell", "table": "s3.2", "row": 7, "col": 0, "text": "x"}], "r7c0"),
    ([{"op": "delete_slide", "id": "s1"}, {"op": "delete_slide", "id": "s2"},
      {"op": "delete_slide", "id": "s3"}, {"op": "delete_slide", "id": "s4"}], "남지 않습니다"),
    ([{"op": "move_slide", "id": "s2", "after": "s5"}], "s5"),
    ([{"op": "set_cell", "table": "s3.2", "row": 0, "col": 0, "text": "ok"},
      {"op": "shrink"}], "모르는 op"),
])
def test_one_bad_op_changes_nothing(ops, said):
    with pytest.raises(pptx_edit.PptxError, match=said):
        pptx_edit.apply(company(), ops)


def test_what_is_not_a_pptx_is_refused_and_a_blank_deck_is_16_9():
    with pytest.raises(pptx_edit.PptxError):
        pptx_edit.outline(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    with pytest.raises(pptx_edit.PptxError):
        pptx_edit.outline(b"PK\x03\x04 not a zip")
    assert pptx_doc.file_format(b"\xd0\xcf\x11\xe0") == ""
    blank = pptx_doc.blank()
    prs = _prs(blank)
    assert prs.slide_width * 9 == prs.slide_height * 16 or abs(
        prs.slide_width / prs.slide_height - 16 / 9) < 0.001
    assert pptx_edit.outline(blank) == (
        "슬라이드 1장\n── s1 (레이아웃: Title Slide) ──\ns1.1 (제목): (비어 있음)\n"
        "s1.2 (부제목): (비어 있음)\n빈칸: 없음"
    )
    # the stretched title placeholder spans the wider slide
    title = prs.slides[0].shapes.title
    assert title.left + title.width < prs.slide_width and title.width > prs.slide_width * 0.7


def test_a_new_deck_is_written_in_the_house_look_with_blanks_left_as_brackets():
    data = pptx_doc.deck("사업 계획 보고", "[부서명] · [YYYY. MM. DD.]", [
        {"title": "추진 배경", "bullets": ["매출 [전년 대비]", "고객 [고객 수]명"],
         "notes": "메모"},
        {"title": "일정", "rows": [["단계", "기간"], ["1단계", "[기간]"]]},
    ], closing="감사합니다")
    text = pptx_edit.outline(data)
    assert "슬라이드 4장" in text and "s2.1 (제목): 추진 배경" in text
    assert "s3.2 (표 2×2)" in text and "s2 메모: 메모" in text
    assert pptx_edit.blanks(data) == [
        "[부서명]", "[YYYY. MM. DD.]", "[전년 대비]", "[고객 수]", "[기간]",
    ]
    described = pptx_doc.describe(data, "pptx")
    assert described["slides"] == 4 and "추진 배경" in described["text"]


def test_a_cell_given_as_a_value_or_a_number_is_still_written():
    result = pptx_edit.apply(company(), [
        {"op": "set_cell", "table": "s3.2", "row": 2, "col": 1, "value": "2027. 4.~9."},
        {"op": "set_cell", "table": "s3.2", "row": 2, "col": 2, "text": 300},
        {"op": "replace_text", "id": "s2.1", "text": ["추진 배경", "(초안)"]},
    ])
    prs = _prs(result.data)
    table = prs.slides[2].shapes[1].table
    assert (table.cell(2, 1).text, table.cell(2, 2).text) == ("2027. 4.~9.", "300")
    assert prs.slides[1].shapes.title.text_frame.text == "추진 배경\n(초안)"
    with pytest.raises(pptx_edit.PptxError, match="text이 없습니다"):
        pptx_edit.apply(company(), [{"op": "set_cell", "table": "s3.2", "row": 1, "col": 1}])


def test_a_written_deck_has_no_automatic_bullet_under_its_written_ones():
    data = pptx_doc.deck("보고", "", [{"title": "배경", "bullets": ["하나", "둘"]}])
    body = next(
        shape for shape in _prs(data).slides[1].shapes
        if shape.has_text_frame and "하나" in shape.text_frame.text
    )
    for paragraph in body.text_frame.paragraphs:
        assert paragraph._p.find(qn("a:pPr")).find(qn("a:buNone")) is not None
    # a line added later copies its neighbour, bullet setting and all
    added = pptx_edit.apply(data, [
        {"op": "replace_text", "id": "s2.2", "text": "• 하나\n• 둘\n• 셋"},
    ])
    body = next(
        s for s in _prs(added.data).slides[1].shapes
        if s.has_text_frame and "셋" in s.text_frame.text
    )
    assert all(
        p._p.find(qn("a:pPr")).find(qn("a:buNone")) is not None
        for p in body.text_frame.paragraphs
    )
