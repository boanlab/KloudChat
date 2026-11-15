"""A report handed over as an HWP or Word file: a work document carries no 「[n]」 in its text
and names the files it was built from in a 「근거 자료」 list; academic writing keeps its numbers
and reference list; no 「(확인 필요)」 is left in the file — a marked value is a blank named by
its field, a marked claim without a value goes."""

from app.services import office_finish, report_office

SOURCES = [
    {"ordinal": 1, "title": "주간회의록_20261110.docx", "origin": "file",
     "publisher": "프로젝트 파일"},
    {"ordinal": 2, "title": "매출실적_2026Q4.xlsx", "origin": "file", "publisher": "프로젝트 파일"},
    {"ordinal": 3, "title": "안 쓴 파일.docx", "origin": "file", "publisher": "프로젝트 파일"},
]
SECTIONS = [
    {"id": "a", "heading": "주요 업무", "content": "초안은 11/17까지 제출합니다 [1]. 10월 매출은 "
                                              "목표를 넘었습니다[2]."},
    {"id": "b", "heading": "실적", "content": "| 월 | 매출 |\n|---|---|\n| 10월 | 4.2억 [2] |"},
]


def test_plain_sources_by_kind_of_document():
    assert office_finish.plain_sources("status", hwp=False, scholarly=False)
    assert office_finish.plain_sources("minutes", hwp=True, scholarly=False)
    assert office_finish.plain_sources("", hwp=True, scholarly=False)
    assert not office_finish.plain_sources("paper", hwp=True, scholarly=True)
    assert not office_finish.plain_sources("", hwp=False, scholarly=True)
    assert not office_finish.plain_sources("", hwp=True, scholarly=True)


def test_a_work_file_names_its_sources_at_the_end():
    data = office_finish.for_file({"sections": SECTIONS, "sources": SOURCES},
                                  format_id="status", hwp=True, scholarly=False)
    body = "\n".join(s["content"] for s in data["sections"])
    assert "[1]" not in body and "[2]" not in body
    assert "제출합니다. 10월" in body and "| 4.2억 |" in body
    last = data["sections"][-1]
    assert last["heading"] == "근거 자료"
    assert last["content"] == "- 주간회의록_20261110.docx\n- 매출실적_2026Q4.xlsx"
    # the file builds no 참고문헌 of its own
    exported = report_office._sections(data)
    assert [s["heading"] for s in exported] == ["주요 업무", "실적", "근거 자료"]


def test_a_closing_document_puts_the_list_on_its_last_section():
    data = office_finish.for_file(
        {"sections": SECTIONS[:1], "sources": SOURCES, "titleBlock": {"closing": True}},
        format_id="official", hwp=True, scholarly=False)
    assert len(data["sections"]) == 1
    assert data["sections"][0]["content"].endswith("※ 근거 자료: 주간회의록_20261110.docx, "
                                                   "매출실적_2026Q4.xlsx")


def test_academic_writing_keeps_numbers_and_its_reference_list():
    data = office_finish.for_file({"sections": SECTIONS, "sources": SOURCES},
                                  format_id="", hwp=False, scholarly=True)
    assert "[1]" in data["sections"][0]["content"] and "sourceList" not in data
    assert report_office._sections(data)[-1]["heading"] == "참고문헌"


def test_check_marks_become_named_blanks():
    settle = office_finish.settle_marks
    assert settle("처리량은 2.3배(확인 필요) 높습니다.") == "처리량은 [처리량] 높습니다."
    assert settle("vLLM은 기존 대비 24배(확인 필요) 빠릅니다.") == \
        "vLLM은 기존 대비 [배수] 빠릅니다."
    assert settle("이 방식은 2023년(확인 필요)에 나왔습니다.") == "이 방식은 [연도]에 나왔습니다."
    # a marked claim with no value goes with its sentence; the rest of the line stays
    assert settle("첫 문장입니다. 업계 표준이 되었습니다(확인 필요). 끝 문장입니다.") == \
        "첫 문장입니다. 끝 문장입니다."
    table = ("| 항목 | 지연 시간 |\n|---|---|\n| TTFT | 120ms(확인 필요) |\n"
             "| 처리량 | 높음 (확인 필요) |")
    assert settle(table) == ("| 항목 | 지연 시간 |\n|---|---|\n| TTFT | [지연 시간] |\n"
                             "| 처리량 | [지연 시간] |")
    assert settle("```\nx = 1 (확인 필요)\n```") == "```\nx = 1 (확인 필요)\n```"
    data = office_finish.for_file(
        {"sections": [{"id": "a", "heading": "관련 연구", "content": "처리량은 2.3배(확인 필요)."}],
         "sources": []}, format_id="", hwp=False, scholarly=True)
    assert "확인 필요" not in data["sections"][0]["content"]
