"""verify1 NEW-6 (STAFF-01, STAFF-10, R-02): what an answer works out from given values stays.

The grounding trace blanked values that are plain arithmetic of grounded ones
(「22시간을 제외하면 49.5시간, 544,500원」), dates counted from a given date by the material's
own offsets (「개강 3월 2일, 30일 전」 → 2027년 1월 31일), and cells copied from the attached
sheet under a 「(천원)」 header.
Invented values are still blanked.
"""

from __future__ import annotations

from pathlib import Path

from app.services import check_skills, files, skill_scripts

FIX = Path(__file__).parent / "fixtures"
TOOL_NUMBERS = check_skills.options_for(None, {"tool_numbers": True}, surface="chat")


def _trace():
    return skill_scripts.entry("grounding-check")


def _numbers(found: list[dict]) -> list[str]:
    return [f["text"] for f in found if f["kind"] == "number"]


# ── STAFF-01: payroll ────────────────────────────────────────────────────

PAYROLL_REQUEST = ("근로장학생 근무현황 엑셀이에요. 학생별로 9월 근무시간 합계 내 주시고, "
                   "시간당 11,000원 곱해서 지급액도 계산해 주세요. 이상한 행(중복, 학번 오류, "
                   "날짜 누락 등)은 따로 표시해 주세요. 엑셀로 받고 싶어요.")
PAYROLL = (f"{PAYROLL_REQUEST}\n"
           f"{files._from_xlsx((FIX / 'payroll' / '근로장학생_근무현황.xlsx').read_bytes())}")
PAYROLL_ANSWER = (FIX / "derived" / "payroll_answer.md").read_text()
PAYROLL_STDOUT = (FIX / "derived" / "payroll_stdout.txt").read_text()


def test_a_total_less_one_row_and_its_pay_stay():
    for options in (TOOL_NUMBERS, {}):
        found = _trace().check(PAYROLL_ANSWER, PAYROLL, [PAYROLL_STDOUT], options)
        assert _numbers(found) == [], options


def test_an_invented_payroll_figure_is_still_blanked():
    wrong = PAYROLL_ANSWER.replace("49.5시간, 544,500원", "52.5시간, 612,000원")
    found = _trace().check(wrong, PAYROLL, [PAYROLL_STDOUT], TOOL_NUMBERS)
    assert _numbers(found) == ["52.5시간", "612,000원"]


# ── R-02: cells copied from the attached sheet ───────────────────────────

GRANT_REQUEST = ("첨부한 연구비 파일에서 연도·항목별 예산 대비 집행률을 표로 정리하고, 연도별로 "
                 "항목 집행률을 비교하는 막대 차트 하나 만들어 줘. 논문실적 시트는 연도별 "
                 "SCIE+KCI 합계 추이도 같이.")
GRANT_SHEET = files._from_xlsx((FIX / "derived" / "grant.xlsx").read_bytes())
GRANT = f"{GRANT_REQUEST}\n{GRANT_SHEET}"
GRANT_ANSWER = (FIX / "derived" / "grant_answer.md").read_text()
GRANT_STDOUT = (FIX / "derived" / "grant_stdout.txt").read_text()


def test_sheet_cells_and_their_totals_under_a_thousand_won_header_stay():
    found = _trace().check(GRANT_ANSWER, GRANT, [GRANT_STDOUT], TOOL_NUMBERS)
    assert _numbers(found) == []
    # Without the code's printout the sheet's own cells still stand where the row is named.
    found = _trace().check(GRANT_ANSWER, GRANT, [], TOOL_NUMBERS)
    assert not {"42,000", "40,150", "20,000천원", "14,200천원"} & set(_numbers(found))


def test_a_sheet_cell_moved_to_another_row_is_still_blanked():
    wrong = GRANT_ANSWER.replace("| 2024 | 인건비 | 42,000 | 40,150 |",
                                 "| 2024 | 인건비 | 42,500 | 40,150 |")
    found = _trace().check(wrong, GRANT, [GRANT_STDOUT], TOOL_NUMBERS)
    assert _numbers(found) == ["42,500"]


# ── STAFF-10 Q3: dates counted from the person's date ────────────────────

RULES = ("[장학금지급규정.pdf]\n제7조(신청 기간) 장학금 신청은 매 학기 개시 30일 전부터 "
         "14일 전까지 학생포털로 한다.")
QUESTION = "2027학년도 1학기 장학금 신청은 언제부터 언제까지예요? 개강은 3월 2일이에요."
DATES_ANSWER = """- 신청 기간: 「매 학기 개시 30일 전부터 14일 전까지」[7].
- 시작일: 2027년 3월 2일에서 30일 전은 2027년 1월 31일(일)입니다.
- 마감일: 2027년 3월 2일에서 14일 전은 2027년 2월 16일(화)입니다.
- 신청 기간은 2027년 1월 31일부터 2월 16일까지입니다."""


def test_dates_counted_from_a_given_date_by_the_materials_offsets_stay():
    given = f"{QUESTION}\n{RULES}"
    found = _trace().check(DATES_ANSWER, given, [],
                           {**TOOL_NUMBERS, "today": "2026-10-11", "person": given})
    assert [f["text"] for f in found] == []


def test_a_date_no_offset_explains_is_still_blanked():
    wrong = DATES_ANSWER.replace("2027년 1월 31일(일)입니다", "2027년 1월 20일(수)입니다")
    found = _trace().check(wrong, f"{QUESTION}\n{RULES}", [], {"today": "2026-10-11"})
    assert [f["text"] for f in found if f["kind"] == "date"] == ["2027년 1월 20일(수)"]
