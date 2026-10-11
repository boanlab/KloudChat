"""Weekdays and dates an answer writes, set right by code (staff sweep STAFF-03/04/10).

A model counts weekdays and day offsets in its head and misses: 「10월 15일(수)」 for a
Thursday, 「2027-02-01(일)」 for 「개강 2027-03-02, 30일 전」 (2027-01-31). Every weekday beside
a date is the calendar's, in a chat answer and in a report; with 날짜 계산 on the turn, a date the
person's own relative words resolve to is the script's.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from app.pipeline import registry
from app.pipeline.bundle import Bundle, Document, Material, Request
from app.pipeline.stage import Context
from app.services import internal_words, post_checks, report, skill_scripts, weekdays

TODAY = dt.date(2026, 10, 11)


def test_every_written_weekday_is_the_calendars():
    text = "회의는 10월 15일(수), 17일(금), 20일(월) 오후 2시에 엽니다."
    fixed, fixes = weekdays.fix_weekdays(text, today=TODAY)
    assert fixed == "회의는 10월 15일(목), 17일(토), 20일(화) 오후 2시에 엽니다."
    assert [f.fixed for f in fixes] == ["10월 15일(목)", "17일(토)", "20일(화)"]
    # Every spelling of a date takes the same check; a right weekday is left alone.
    assert weekdays.fix_weekdays("2027-02-01(일)부터", today=TODAY)[0] == "2027-02-01(월)부터"
    assert weekdays.fix_weekdays("2027. 2. 1.(일)", today=TODAY)[0] == "2027. 2. 1.(월)"
    assert weekdays.fix_weekdays("10/15(수)", today=TODAY)[0] == "10/15(목)"
    assert weekdays.fix_weekdays("10월 15일 수요일에", today=TODAY)[0] == "10월 15일 목요일에"
    assert weekdays.fix_weekdays("2027년 1월 31일(일) ~ 2월 16일(화)", today=TODAY)[1] == []
    # 「매주 수요일마다」 is not a date's weekday; nor is anything in a code block.
    assert weekdays.fix_weekdays("매주 수요일마다 10월 15일 회의", today=TODAY)[1] == []
    assert weekdays.fix_weekdays("```\n10월 15일(수)\n```", today=TODAY)[1] == []


def test_the_year_comes_from_the_text_then_the_material_then_today():
    # The person's words name 2027: 4월 13일 is a Tuesday then, though a Monday in 2026.
    text = "중간고사 4월 13일(월)~19일(일)"
    assert weekdays.fix_weekdays(text, context="2027학년도 1학기 교무위원회", today=TODAY)[0] \
        == "중간고사 4월 13일(화)~19일(월)"
    # No year anywhere and half a year from today: the weekday may be right for either year.
    assert weekdays.fix_weekdays(text, today=TODAY)[1] == []
    # An explicit year earlier in the answer carries to the days after it.
    assert weekdays.fix_weekdays("2027년 4월 13일(월), 19일(일)", today=TODAY)[0] \
        == "2027년 4월 13일(화), 19일(월)"


def _bundle(answer: str, request: str = "회의록 양식 알려 줘") -> Bundle:
    return Bundle(request=Request(text=request, model="w", api_key="k", today=TODAY),
                  material=Material(given=""),
                  document=Document(streamed=answer, answer=answer))


def test_a_chat_answer_has_its_weekdays_and_tool_apologies_mended():
    bundle = _bundle("죄송합니다. 방금 계산 도구를 잘못 호출했습니다. "
                     "다음 회의는 10월 15일(수)입니다.")
    asyncio.run(registry.stage("answer-normalize").apply(bundle, Context()))
    assert bundle.document.answer == "다음 회의는 10월 15일(목)입니다."


def test_a_tool_apology_goes_but_a_tool_failure_that_matters_stays():
    assert internal_words.strip_tool_apologies("계산 도구를 잘못 불러왔습니다. 합계는 3입니다.") \
        == "합계는 3입니다."
    kept = "코드 실행 도구 호출에 실패해 표를 만들지 못했습니다."
    assert internal_words.strip_tool_apologies(kept) == kept
    used = "검색 도구를 사용했습니다."
    assert internal_words.strip_tool_apologies(used) == used


def test_a_reports_weekdays_are_mended_in_the_final_normalisation():
    sections = [{"id": "s1", "heading": "일정", "content": "개강은 2027년 3월 2일(월)입니다."}]
    out = report.normalize_document(sections, request="개강 일정 보고서")
    assert "2027년 3월 2일(화)" in out[0]["content"]


# ── 날짜 계산 on the turn: the answer's dates are the script's ────────────────

REQUEST = "개강 2027-03-02, 30일 전부터 14일 전까지 신청을 받아요. 며칠(요일)부터 며칠(요일)까지?"


def test_an_offset_after_a_date_counts_from_that_date():
    rows = skill_scripts.module("date-calc", "dates").scan(REQUEST, TODAY)
    shown = {r["text"]: r["shown"] for r in rows}
    assert shown["30일 전"] == "2027-01-31(일)" and shown["14일 전"] == "2027-02-16(화)"
    # In another sentence the offset is the meeting's (or today's) again.
    again = skill_scripts.module("date-calc", "dates").scan(
        "개강은 3월 2일입니다. 2주 뒤 보고", TODAY)
    assert next(r for r in again if r["text"] == "2주 뒤")["date"] == "2026-10-25"


def test_the_date_skill_sets_the_answers_near_misses_and_blanks_to_the_scripts_dates():
    assert post_checks.entries("date-calc") == [("date-calc", "dates")]
    dates = skill_scripts.module("date-calc", "dates")
    args = {"request": REQUEST, "material": "", "today": TODAY.isoformat()}
    missed = dates.post("신청 기간은 2027-02-01(일)부터 2027-02-17(화)까지입니다.", args)
    assert missed["text"] == "신청 기간은 2027-01-31(일)부터 2027-02-16(화)까지입니다."
    blank = dates.post("30일 전은 [일자], 14일 전은 [일자]입니다.", args)
    assert blank["text"] == "30일 전은 2027-01-31(일), 14일 전은 2027-02-16(화)입니다."
    right = "2027년 1월 31일(일) ~ 2월 16일(화)"
    assert dates.post(right, args)["text"] == right and not dates.post(right, args)["fixed"]


# ── verify1 N1: the source's weekday and the academic year ─────────────────


def test_a_weekday_the_source_states_for_that_day_is_left_alone():
    # The transcript says 「3월 2일 화요일」 for 2027학년도; the header's 2026 date is not its year.
    text = "**일시:** 2026년 10월 7일(수)\n## 2027학년도 학사일정\n1학기 개강 3월 2일(화)"
    fixed, fixes = weekdays.fix_weekdays(
        text, context="2027학년도 1학기 개강은 3월 2일 화요일", today=TODAY)
    assert fixed == text and fixes == []
    # Without the source's word, the academic-year heading alone sets the year.
    fixed, fixes = weekdays.fix_weekdays(text, today=TODAY)
    assert fixed == text and fixes == []
    # A wrong weekday under that heading is still set to 2027's calendar.
    wrong = "## 2027학년도 학사일정\n1학기 개강 3월 2일(월)"
    assert weekdays.fix_weekdays(wrong, today=TODAY)[0] \
        == "## 2027학년도 학사일정\n1학기 개강 3월 2일(화)"


def test_an_academic_year_runs_from_march_to_february():
    # 2027학년도 겨울 방학 끝, 2월 28일 is in 2028 (a Monday).
    text = "2027학년도 2학기 종강 12월 17일(금), 2월 28일(월) 학년도 종료"
    fixed, fixes = weekdays.fix_weekdays(text, today=TODAY)
    assert fixes == [] and fixed == text


def test_a_weekday_right_for_a_year_the_request_names_is_not_bent_to_another():
    # The answer's own last full date is 2026; the request's year, 2027, fits the written weekday.
    text = "작성일 2026년 10월 11일(일)\n개강 3월 2일(화)"
    assert weekdays.fix_weekdays(text, context="2027년 3월 2일 개강", today=TODAY)[1] == []


# ── verify1 N2: plain chat's date arithmetic is checked by code ───────────────

N16 = ("2027학년도 1학기 개강이 2027년 3월 2일이에요. 개강 30일 전부터 14일 전까지가 장학금 신청 "
       "기간이면 정확히 며칠(요일)부터 며칠(요일)까지예요?")
N16_ANSWER = ("2027년 3월 2일(화) 기준, 개강 30일 전부터 14일 전까지는 2027년 2월 1일(월)부터 "
              "2월 17일(수)까지입니다.")
Q3 = "2027학년도 1학기 장학금 신청은 언제부터 언제까지예요? 개강은 3월 2일이에요."
REGULATION = "제7조(신청 기간) 장학금 신청은 매 학기 개시 30일 전부터 14일 전까지 학생포털로 한다."


def test_an_offset_from_an_event_dated_in_another_sentence_counts_from_that_date():
    rows = skill_scripts.module("date-calc", "dates").scan(N16, TODAY)
    shown = {r["text"]: r["shown"] for r in rows}
    assert shown["30일 전"] == "2027-01-31(일)" and shown["14일 전"] == "2027-02-16(화)"
    # The regulation's 「학기 개시」 is the 개강 the person dated on another line.
    rows = skill_scripts.module("date-calc", "dates").scan(
        f"{Q3}\n{REGULATION}", TODAY)
    shown = {r["text"]: r["shown"] for r in rows}
    assert shown["30일 전"] == "2027-01-31(일)" and shown["14일 전"] == "2027-02-16(화)"


def test_plain_chat_date_arithmetic_is_set_to_the_calendar_before_weekdays_are():
    bundle = _bundle(N16_ANSWER, request=N16)
    asyncio.run(registry.stage("answer-normalize").apply(bundle, Context()))
    assert bundle.document.answer == (
        "2027년 3월 2일(화) 기준, 개강 30일 전부터 14일 전까지는 2027년 1월 31일(일)부터 "
        "2월 16일(화)까지입니다.")


def test_an_answer_that_works_from_the_persons_date_is_checked_against_its_own_offsets():
    # The offsets are the regulation's (in the material), the date the person's.
    bundle = _bundle("- 시작일: 2027년 3월 2일에서 30일 전은 2027년 2월 1일(월)입니다.\n"
                     "- 마감일: 2027년 3월 2일에서 14일 전은 2027년 2월 17일(수)입니다.",
                     request=Q3)
    asyncio.run(registry.stage("answer-normalize").apply(bundle, Context()))
    assert "2027년 1월 31일(일)" in bundle.document.answer
    assert "2027년 2월 16일(화)" in bundle.document.answer


def test_a_date_no_offset_of_the_persons_explains_is_left_to_the_weekday_check():
    # 「30일 이내」 with no date of the person's to count from is not today's arithmetic.
    bundle = _bundle("마감은 2026년 11월 12일(목)입니다.",
                     request="신청 후 30일 이내 처리해요. 마감 언제예요?")
    asyncio.run(registry.stage("answer-normalize").apply(bundle, Context()))
    assert bundle.document.answer == "마감은 2026년 11월 12일(목)입니다."


def test_date_arithmetic_on_a_given_date_does_not_force_a_web_search():
    from app.services import context

    assert not context.needs_web_search(N16)
    assert context.search_plan("auto", N16) == (True, None)
    # A schedule the person did not give is still looked up.
    assert context.needs_web_search("2027학년도 1학기 개강일은 언제예요?")
