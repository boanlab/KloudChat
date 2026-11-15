"""A weekly report built from meeting records: weekdays from the calendar, a quantity the
sources give two ways reported as open, action rows holding their source item's owner and day."""

# ruff: noqa: E501
from __future__ import annotations

import asyncio
import datetime as dt

from app.pipeline import report_stages
from app.pipeline.bundle import Bundle, Part, Request
from app.pipeline.stage import Context
from app.services import action_rows, report, source_conflicts, weekdays

MINUTES_1103 = """- 참석: 이준호 본부장, 강민재 마케팅팀장
이준호 본부장이 「별빛 서치」 제안서 초안을 보고했다. 총사업비는 3.2억 원(개발 1.8억, 인프라 0.9억)이다.

| 항목 | 담당 | 기한 |
|---|---|---|
| 제안서 인력 계획·경쟁 비교 보완 | 이준호 | 11월 13일 |
| 마케팅 10월 집행액 정리 | (미정) | 11월 10일 |
"""
PROPOSAL = "신규사업 제안서\n총 사업비 3.5억 원\n일정 2028년 상반기 출시\n"
MINUTES_1110 = """주간 경영회의록 (2026-11-10)
실행 항목
항목
담당
기한
프로모션 안내문
강민재
11월 20일
보안 점검 일정 공지
임하준
11월 17일
"""
FILES = [("04_경영회의록_2026-11-03.md", MINUTES_1103), ("07_제안서.pptx", PROPOSAL),
         ("05_경영회의록_2026-11-10.hwpx", MINUTES_1110)]
BLOCKS = ["# 프로젝트 지식\n\n" + "\n\n".join(f"## {n}\n{t}" for n, t in FILES)]


# ── the weekday is the calendar's, whatever other years the material names ──


def test_a_weekday_in_a_table_cell_is_set_by_the_calendar_not_by_a_far_off_year_in_the_material():
    # 11/20/2026 is a Friday; 11/20/2028, a year the proposal names, is a Monday
    assert dt.date(2026, 11, 20).weekday() == 4 and dt.date(2028, 11, 20).weekday() == 0
    text = ("| 일정 | 내용 |\n| --- | --- |\n| 11월 20일(월) | 제안서 상정 |\n"
            "| 11월 24일(화)~28일(토) | 점검 |\n\n11월 20일(월)에 올린다.")
    fixed, fixes = weekdays.fix_weekdays(
        text, context="2026년 11월 보고\n2026년 분기 실적\n2028년 상반기 출시 목표", today=dt.date(2026, 11, 15))
    assert "| 11월 20일(금) |" in fixed and "11월 20일(금)에 올린다" in fixed
    assert "11월 24일(화)~28일(토)" in fixed
    assert len(fixes) == 2


def test_a_far_date_with_a_weekday_right_in_a_named_year_is_still_left_alone():
    # a 2028 plan's date, far from today, keeps the weekday that is right in 2028
    text = "11월 20일(월)에 출시한다."
    fixed, fixes = weekdays.fix_weekdays(text, context="2026년 보고\n2028년 출시",
                                         today=dt.date(2026, 3, 1))
    assert fixed == text and not fixes


# ── two sources, two values ──


def test_a_label_two_files_give_differently_is_found():
    found = source_conflicts.conflicts(source_conflicts.source_files(BLOCKS))
    assert [c.label for c in found] == ["총사업비"]
    values = dict(found[0].values)
    assert set(values) == {"3.2억 원", "3.5억 원"}
    assert values["3.5억 원"] == "07_제안서.pptx"


def test_a_series_in_one_file_or_a_unit_less_number_is_no_conflict():
    files = [("a.md", "분기 매출 100만 원, 매출 120만 원"), ("b.md", "매출 400만 원"),
             ("c.md", "합계 2,590"), ("d.md", "합계 4,570")]
    assert source_conflicts.conflicts(files) == []


def test_a_report_stating_one_value_gets_both_with_their_files_and_a_named_blank():
    found = source_conflicts.conflicts(source_conflicts.source_files(BLOCKS))
    sections = [{"heading": "사업", "content": "별빛 서치의 총사업비는 3.2억 원으로 확인되며, "
                                              "이 금액을 기준으로 진행합니다."},
                {"heading": "참고문헌", "content": "총사업비 3.5억 원"}]
    marked, hit = source_conflicts.mark(sections, found, report._REFERENCE_HEADING)
    text = marked[0]["content"]
    assert len(hit) == 1
    assert "3.2억 원" in text and "3.5억 원" in text
    assert "04_경영회의록_2026-11-03.md" in text and "07_제안서.pptx" in text
    assert "[총사업비 확정 필요]" in text and "확인 필요" in text


def test_a_report_giving_both_values_is_left_as_written():
    found = source_conflicts.conflicts(source_conflicts.source_files(BLOCKS))
    sections = [{"heading": "사업", "content": "회의록은 총사업비 3.2억 원, 제안서는 총사업비 3.5억 원이다."}]
    marked, hit = source_conflicts.mark(sections, found, report._REFERENCE_HEADING)
    assert marked == sections and not hit


def test_a_table_cell_stating_one_value_gets_the_note_under_the_table():
    found = source_conflicts.conflicts(source_conflicts.source_files(BLOCKS))
    sections = [{"heading": "사업", "content": "| 항목 | 값 |\n|---|---|\n| 총사업비 | 3.2억 원 |\n\n끝."}]
    marked, hit = source_conflicts.mark(sections, found, report._REFERENCE_HEADING)
    lines = marked[0]["content"].split("\n")
    assert hit and lines[3] == "" and lines[4].startswith("※ 확인 필요: 총사업비")


def _bundle(sections):
    bundle = Bundle(request=Request(text="주간 보고서", model="m", api_key="k"))
    bundle.material.parts = [Part(b, "attachment") for b in BLOCKS]
    bundle.document.sections = sections
    return bundle


def test_the_stage_marks_the_section_and_says_so():
    bundle = _bundle([{"id": "s1", "heading": "사업", "content": "총사업비는 3.2억 원이다."}])

    async def run():
        return [e async for e in report_stages.SourceConflicts().run(bundle, Context())]

    events = asyncio.run(run())
    assert "총사업비는 3.2억 원이다. ※ 확인 필요" in bundle.document.sections[0]["content"]
    assert events[0]["id"] == "source-conflicts" and events[1]["type"] == "section"


# ── action rows ──


def test_the_sources_action_items_are_read_from_a_table_and_from_one_cell_per_line():
    items = action_rows.items_in(FILES)
    by_task = {i.task: (i.owner, i.due) for i in items}
    assert by_task["제안서 인력 계획·경쟁 비교 보완"] == ("이준호", "11월 13일")
    assert by_task["마케팅 10월 집행액 정리"] == ("", "11월 10일")
    assert by_task["프로모션 안내문"] == ("강민재", "11월 20일")
    assert by_task["보안 점검 일정 공지"] == ("임하준", "11월 17일")


def test_a_plan_table_without_owners_gets_the_sources_owners_and_blanks_where_none_is_named():
    items = action_rows.items_in(FILES)
    table = ("| 일정 | 내용 |\n| --- | --- |\n"
             "| 11월 17일(화) | 마케팅팀 10월 집행액 정리 완료 |\n"
             "| 11월 17일(화) | 정기 보안 점검 일정 공지 |\n"
             "| 11월 20일(금) | 연말 프로모션 안내문 배포 |\n"
             "| 11월 24일(화)~28일(토) | 하반기 정기 보안 점검 실시 |")
    rows = action_rows.fix(table, items, "주간 보고서").split("\n")
    assert rows[0] == "| 일정 | 내용 | 담당 |" and rows[1].count("---") == 3
    # a day the writer made up for a sourced task goes back to the source's, marked
    assert rows[2] == "| 11월 10일(원 기한, 확인 필요) | 마케팅팀 10월 집행액 정리 완료 | [담당자] |"
    assert rows[3].endswith("| 임하준 |") and rows[3].startswith("| 11월 17일(화) |")
    assert rows[4].endswith("| 강민재 |")
    # the inspection itself is no item of the sources: nothing is attached to it
    assert rows[5].endswith("| - |") and "11월 24일(화)~28일(토)" in rows[5]


def test_an_owner_nobody_named_for_a_sourced_task_is_replaced_and_an_empty_one_filled():
    items = action_rows.items_in(FILES)
    table = ("| 항목 | 담당 | 기한 |\n| --- | --- | --- |\n"
             "| 프로모션 안내문 | 보안팀 | 11월 20일 |\n"
             "| 보안 점검 일정 공지 |  | 11월 17일 |\n"
             "| 마케팅 10월 집행액 정리 |  | 11월 10일 |")
    rows = action_rows.fix(table, items).split("\n")
    assert "| 강민재 |" in rows[2]
    assert "| 임하준 |" in rows[3]
    assert "| [담당자] |" in rows[4]


def test_an_owner_or_day_the_request_gives_is_kept():
    items = action_rows.items_in(FILES)
    table = ("| 항목 | 담당 | 기한 |\n| --- | --- | --- |\n| 프로모션 안내문 | 김하늘 | 11월 25일 |")
    given = "프로모션 안내문은 김하늘이 11월 25일까지"
    assert action_rows.fix(table, items, given) == table


def test_a_row_that_only_shares_a_word_is_not_an_item():
    items = action_rows.items_in(FILES)
    assert not action_rows.restates("보안 점검 일정 공지", "하반기 정기 보안 점검 실시")
    assert action_rows.fix("| 구분 | 내용 |\n|---|---|\n| 가 | 보안 점검 |", items) \
        == "| 구분 | 내용 |\n|---|---|\n| 가 | 보안 점검 |"
