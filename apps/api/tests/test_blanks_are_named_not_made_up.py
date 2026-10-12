"""What the request and material do not give is a bracket blank naming it (「[담당자]」), never
made up; checks read one as a valid gap and count it for the reader."""

from __future__ import annotations

import asyncio
import io
import zipfile

from app.services import (
    cover_letter,
    doc_formats,
    placeholders,
    quality_gate,
    report,
    report_checks,
    report_export,
)
from app.services.verify import Doc

MEMO = (
    "- 포스터 접수 현황 확인 필요\n- 배너 시안: 수아, 10/23까지\n"
    "- 다과 업체: 다음 회의에서 재논의\n"
)
ACTIONS = (
    "| 할 일 | 담당 | 기한 |\n|---|---|---|\n"
    "| 포스터 접수 현황 확인 | 조교실 | 2026-10-30(금) |\n"
    "| 배너 시안 | 수아 | 10/23 |\n"
    "| 다과 업체 선정 | 미정(다음 회의에서 재논의) | 미정(다음 회의에서 재논의) |\n"
)


def test_an_owner_the_minutes_material_never_names_becomes_a_blank():
    sections = [{"heading": "실행 항목", "content": ACTIONS}]
    fixed, codes = report_checks.minutes_grounding(sections, MEMO)
    content = fixed[0]["content"]
    assert "| 포스터 접수 현황 확인 | [담당자] |" in content
    assert "| 배너 시안 | 수아 |" in content
    assert codes == ["minutes_owner"]


def test_an_unsourced_due_date_becomes_a_blank_named_by_its_column():
    rows = report._unsourced_owner_dates(ACTIONS, MEMO).splitlines()
    assert rows[2] == "| 포스터 접수 현황 확인 | [담당자] | [기한] |"
    assert rows[3] == "| 배너 시안 | 수아 | 10/23 |"


def test_an_undecided_item_the_material_states_keeps_its_words():
    rows = report._unsourced_owner_dates(ACTIONS, MEMO).splitlines()
    assert rows[4] == "| 다과 업체 선정 | 미정(다음 회의에서 재논의) | 미정(다음 회의에서 재논의) |"
    fixed, _ = report_checks.minutes_grounding([{"heading": "실행 항목", "content": ACTIONS}], MEMO)
    assert "미정(다음 회의에서 재논의) | 미정(다음 회의에서 재논의)" in fixed[0]["content"]


def test_money_in_a_document_with_no_material_is_marked_not_blanked():
    out = report._without_invented_money("행사 예산은 500만 원이고 다과비는 1인당 8,000원입니다.")
    assert out == "행사 예산은 500만 원(확인 필요)이고 다과비는 1인당 8,000원(확인 필요)입니다."
    # a sum the sources state stays as written
    assert report._without_invented_money(
        "예산은 500만 원입니다.", ["행사 예산 500만 원 확정"]) == "예산은 500만 원입니다."


def test_a_head_field_not_given_is_printed_as_its_name_in_brackets():
    block = doc_formats.title_block(
        doc_formats.BY_ID["minutes"], {"fields": {"회의명": "학과 회의", "작성자": "박지민"}},
        "학과 회의 회의록")
    assert dict(block["fields"])["작성자"] == ""
    assert doc_formats.blank("작성자") == "[작성자]"
    assert doc_formats.blank("보고자(부서)") == "[보고자·부서]"
    data = report_export.to_docx("학과 회의", [{"heading": "논의", "content": "본문."}],
                                 title_block=block)
    xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
    assert "[작성자]" in xml and "[일시]" in xml and "(기입)" not in xml


def test_a_blank_passes_the_checks_and_is_counted():
    sections = [{"heading": "실행 항목", "content": (
        "다과 업체는 [담당자]가 [기한]까지 정합니다.\n\n"
        "| 할 일 | 담당 | 기한 | 비고 |\n|---|---|---|---|\n"
        "| 포스터 접수 현황 확인 | [담당자] | [기한] | 자료에 없음 |\n")}]
    found = quality_gate.report_findings(sections)
    assert not [f for f in found if f["code"] in ("placeholder", "broken_glyph_or_cite")]
    assert not report.flagged_sentences(sections, MEMO, [])
    rows, _ = asyncio.run(report_checks.minutes_rows(Doc(sections, MEMO, "회의록", [], [], []), []))
    assert rows == []
    total, named = placeholders.summary(
        [s["content"] for s in sections], {"fields": [["작성자", ""], ["회의명", "학과 회의"]]})
    assert total == 5 and named == "[담당자] 2 · [기한] 2 · [작성자]"


def test_links_citations_and_check_boxes_are_not_blanks():
    text = ("근거는 [보고서](https://example.org)와 [1], [12]입니다. - [ ] 할 일 - [x] 끝난 일 "
            "[[그림: flow | 흐름 | a → b]] [참고] [TBD] [미정] 〔그림〕 [담당자][1]")
    assert not placeholders.found([text])
    assert placeholders.found(["[구체적 성과 수치]와 [담당자]·[기한]"]) == {
        "[구체적 성과 수치]": 1, "[담당자]": 1, "[기한]": 1}
    assert placeholders.is_blank(" **[담당자]** ") and not placeholders.is_blank("[담당자] 외")


def test_a_cover_letter_flag_suggests_the_blank():
    outcome = asyncio.run(cover_letter.post_process(
        "[제출용 답안 1]\n매출을 30% 올렸습니다.", request="자기소개서 써 줘",
        material="동아리 활동"))
    assert outcome is not None
    assert "30" in outcome.flags and "[구체적 성과 수치]" in outcome.detail
