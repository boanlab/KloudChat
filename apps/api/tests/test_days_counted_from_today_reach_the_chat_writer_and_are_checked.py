"""「다음 주 월요일」 in a chat question counted from today by code: given to the writer, and
the day the answer states for it set to that day."""

from __future__ import annotations

import datetime as dt
import time

from app.pipeline import chat_stages
from app.pipeline.bundle import Request
from app.services import date_rules, skill_scripts

#: a Thursday
TODAY = dt.date(2026, 10, 22)


def _checked(answer: str, request: str, today: dt.date = TODAY) -> str:
    return skill_scripts.dates().post(answer, {
        "request": request, "today": today.isoformat(), "anchored_only": True,
        "from_today": True})["text"]


def test_next_mondays_date_in_a_one_line_answer_is_set_to_the_counted_day():
    request = "다음 주 월요일이 며칠이야? 한 줄로 답해줘."
    fixed = _checked("다음 주 월요일은 11월 2일입니다.", request)
    assert fixed == "다음 주 월요일은 10월 26일입니다."


def test_a_full_date_and_its_weekday_follow_the_counted_day():
    assert _checked("다음주 월요일은 2026. 11. 2.(월)입니다.", "다음주 월요일 며칠이야?") == (
        "다음주 월요일은 2026. 10. 26.(월)입니다.")


def test_the_answers_only_date_is_set_when_the_question_asks_for_that_day():
    assert _checked("11월 2일입니다.", "다음 주 월요일이 며칠이야?") == "10월 26일입니다."


def test_a_right_answer_and_todays_own_date_stay():
    answer = "오늘(2026년 10월 22일 목요일) 기준 다음 주 월요일은 10월 26일입니다."
    assert _checked(answer, "다음 주 월요일이 며칠이야?") == answer


def test_tomorrow_the_day_after_and_days_later_are_counted_from_today():
    assert _checked("모레는 10월 25일입니다.", "모레는 며칠이야?") == "모레는 10월 24일입니다."
    assert _checked("3일 후는 10월 26일입니다.", "3일 후가 언제야?") == "3일 후는 10월 25일입니다."
    assert _checked("내일은 10월 22일입니다.", "내일 며칠이야?") == "내일은 10월 23일입니다."


def test_the_week_runs_monday_to_sunday():
    sunday = dt.date(2026, 10, 25)
    assert _checked("다음 주 월요일은 11월 2일입니다.", "다음 주 월요일 며칠?", sunday) == (
        "다음 주 월요일은 10월 26일입니다.")
    assert _checked("이번 주 금요일은 10월 30일입니다.", "이번 주 금요일 며칠?") == (
        "이번 주 금요일은 10월 23일입니다.")


def test_a_date_said_about_something_else_stays():
    answer = "다음 주 월요일 회의는 11월 2일로 미뤄졌습니다."
    assert _checked(answer, "다음 주 월요일 일정 알려줘") == answer
    answer = "이번 주 금요일(10월 23일) 회의 안건: 11월 2일 착공"
    assert _checked(answer, "이번 주 금요일 회의 안건 정리해줘") == answer


def test_a_line_with_its_own_date_is_not_counted_from_today():
    request = "10월 30일 회의에서 다음 주 월요일까지 하자고 했어. 그게 며칠이야?"
    answer = "다음 주 월요일은 11월 2일입니다."
    assert _checked(answer, request) == answer
    assert date_rules.today_block(request, TODAY) == ""


def test_the_chat_writer_is_given_the_counted_days():
    block = date_rules.today_block("다음 주 월요일이 며칠이야? 한 줄로 답해줘.", TODAY)
    assert "2026. 10. 22.(목)" in block
    assert "- 「다음 주 월요일」: 2026. 10. 26.(월)" in block
    # today itself is in the system turn already
    assert date_rules.today_block("안녕, 오늘 기분 어때?", TODAY) == ""
    assert date_rules.today_block("보고서 요약해줘", TODAY) == ""


def test_the_chat_answer_check_sets_the_counted_day():
    req = Request(text="다음 주 월요일이 며칠이야? 한 줄로 답해줘.", model="w", api_key="k",
                  today=TODAY)
    assert chat_stages._dates_from_given("다음 주 월요일은 11월 2일입니다.", req, "") == (
        "다음 주 월요일은 10월 26일입니다.")


def test_fifty_thousand_spaces_finish_quickly():
    started = time.perf_counter()
    _checked(" " * 50_000 + "11월 2일", "다음 주 월요일" + " " * 50_000 + "며칠")
    assert time.perf_counter() - started < 0.5
