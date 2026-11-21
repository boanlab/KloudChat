"""A full date of next year near today (「2027년 1월 2일까지」) does not carry its year onto the
bare dates written after it: near today a bare 「12월 21일(월)」 is this year's.

Round-3 r3c09 (11-21, KC-SA and KC-SL, a moving checklist for 12월 19일): after 「전입신고는 이사
후 14일 이내(2027년 1월 2일까지)」 the weekday fixer read every later bare date in 2027, so the
right 「12월 21일(월)」 became 「(화)」 and 2027 weekdays the writer had slipped into
(「12월 18일(토)」, 「11월 23일(화)」) stood."""

from __future__ import annotations

import datetime as dt

from app.services import weekdays

TODAY = dt.date(2026, 11, 21)


def test_bare_dates_after_a_near_next_year_deadline_are_this_years():
    text = ("잔금은 2027년 1월 8일까지 치르면 됩니다. 12월 23일(수)에 이사하고, 짐은 12월 21일(화)에 쌉니다. "
            "업체 확인은 11월 24일(수)에 하세요.")
    out, _ = weekdays.fix_weekdays(text, context="12월 23일에 이사해.", today=TODAY)
    assert "12월 23일(수)" in out
    assert "12월 21일(월)" in out
    assert "11월 24일(화)" in out


def test_a_far_dated_memo_keeps_its_own_year():
    text = "2025년 3월 4일(화) 회의에서 정했고, 3월 6일(목)에 다시 모였습니다."
    out, fixes = weekdays.fix_weekdays(text, context="", today=TODAY)
    assert out == text and not fixes


def test_a_heading_year_still_sets_the_dates_under_it():
    text = "2027년 행사 일정\n- 12월 3일(금) 송년회\n- 12월 10일(금) 시상식"
    out, fixes = weekdays.fix_weekdays(text, context="", today=TODAY)
    assert out == text and not fixes
