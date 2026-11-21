"""A chat writer gets the weekday of each day the person wrote, counted by code.

Left to work weekdays out itself the writer wrote 「12/22는 월요일, 1/2는 금요일」 for 2026 (both
wrong) and told the person their right 「12월 22일(화)」 did not match the calendar."""

from __future__ import annotations

import datetime as dt

from app.services import date_rules

TODAY = dt.date(2026, 11, 21)
NOTICE = ("- 휴강: 12월 22일(화) ~ 1월 2일(토)\n- 보강: 1월 5일(화)~6일(수) 오후 4시~6시\n"
          "- 겨울 특강 신청: 12월 11일(금)까지, 수강료는 12월 15일까지 납부")


def test_each_written_day_comes_with_its_weekday_in_the_year_it_falls_in():
    block = date_rules.stated_block(NOTICE, TODAY)
    assert "「12월 22일」: 2026. 12. 22.(화)" in block
    assert "「1월 2일」: 2027. 1. 2.(토)" in block
    assert "「1월 5일」: 2027. 1. 5.(화)" in block
    assert "「12월 15일」: 2026. 12. 15.(화)" in block


def test_a_request_without_a_calendar_day_gets_no_block():
    assert date_rules.stated_block("회의록 요약해 줘. 다음 주에 다시 볼게.", TODAY) == ""
