"""「다음 주 월요일」 in a chat question counted from today by code and given to the writer."""

from __future__ import annotations

import datetime as dt
import time

from app.services import date_rules

#: a Thursday
TODAY = dt.date(2026, 10, 22)


def test_a_line_with_its_own_date_is_not_counted_from_today():
    request = "10월 30일 회의에서 다음 주 월요일까지 하자고 했어. 그게 며칠이야?"
    assert date_rules.today_block(request, TODAY) == ""


def test_the_chat_writer_is_given_the_counted_days():
    block = date_rules.today_block("다음 주 월요일이 며칠이야? 한 줄로 답해줘.", TODAY)
    assert "2026. 10. 22.(목)" in block
    assert "- 「다음 주 월요일」: 2026. 10. 26.(월)" in block
    # today itself is in the system turn already
    assert date_rules.today_block("안녕, 오늘 기분 어때?", TODAY) == ""
    assert date_rules.today_block("보고서 요약해줘", TODAY) == ""


def test_the_week_runs_monday_to_sunday():
    sunday = dt.date(2026, 10, 25)
    assert "- 「다음 주 월요일」: 2026. 10. 26.(월)" in date_rules.today_block(
        "다음 주 월요일 며칠?", sunday)
    assert "- 「이번 주 금요일」: 2026. 10. 23.(금)" in date_rules.today_block(
        "이번 주 금요일 며칠?", TODAY)


def test_fifty_thousand_spaces_finish_quickly():
    started = time.perf_counter()
    date_rules.today_block("다음 주 월요일" + " " * 50_000 + "며칠", TODAY)
    assert time.perf_counter() - started < 0.5
