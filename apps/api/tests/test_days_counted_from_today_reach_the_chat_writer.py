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


def test_a_weekday_of_a_months_nth_week_is_counted():
    friday = dt.date(2026, 10, 23)
    block = date_rules.today_block("다음 달 셋째 주 금요일은 몇 월 며칠이야? 오늘부터 며칠 남았어?",
                                   friday)
    # November's first week holds its first Thursday (11/5): its third Friday is 11/20
    assert "- 「다음 달 셋째 주 금요일」: 2026. 11. 20.(금) (오늘 빼고 그날까지 28일)" in block
    assert "2026. 11. 20.(금)" in date_rules.today_block("다음 달 세 번째 금요일 며칠?", friday)
    assert "2026. 11. 27.(금)" in date_rules.today_block("다음 달 마지막 금요일 며칠?", friday)
    assert "2026. 12. 9.(수)" in date_rules.today_block("12월 둘째 주 수요일 며칠?", friday)
    # no fifth Friday in November 2026
    assert date_rules.today_block("다음 달 다섯째 금요일 며칠?", friday) == ""


def test_a_follow_up_leans_on_the_persons_last_turn():
    friday = dt.date(2026, 10, 23)
    asked = "다음 달 셋째 주 금요일은 몇 월 며칠이야? 오늘부터 며칠 남았어?"
    then = "그날까지 오늘은 빼고 하루에 1만 2천 원씩 모으면 모두 얼마가 돼?"
    block = date_rules.today_block(then, friday, [asked])
    assert "오늘 빼고 그날까지 28일" in block
    # a turn naming its own day reads only itself
    assert "11. 20." not in date_rules.today_block("모레는 며칠이야?", friday, [asked])
