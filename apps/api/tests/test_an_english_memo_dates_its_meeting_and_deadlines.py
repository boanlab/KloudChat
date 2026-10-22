"""An English memo dates its meeting and deadlines as a Korean one does: 「Oct 19 … weekly
sync」 is the meeting day (the next meeting's 「Oct 26 (Mon)」 is not), a minutes deadline
「10월 28일」 is the memo's 「Oct 28」, and 「(Mon)」 is a Monday. Shape from the round 16 audit
(H2)."""

from __future__ import annotations

import datetime as dt
import time

from app.services import report_checks, stated_days, weekdays

MEMO = ("Oct 19 Product Design weekly sync. Attendees: Maya Chen (lead), Arjun Patel. "
        "Arjun will finish the event tracking spec by Oct 28. Lena will deliver 40 icons by "
        "October 30th. Next meeting: Oct 26 (Mon) 3pm, Room 4B.")
TODAY = dt.date(2026, 10, 22)


def test_english_days_read_as_numbers():
    assert weekdays.english_days("Next meeting: Oct 26 (Mon) 3pm") == "Next meeting: 10/26(월) 3pm"
    assert weekdays.english_days("by October 30th, then 4 Nov") == "by 10/30, then 11/4"
    # a month name with no day, or a word that only starts like one, stays
    assert weekdays.english_days("in October, Marching band") == "in October, Marching band"


def test_the_memo_s_first_day_is_the_meeting_s_not_the_next_meeting_s():
    assert report_checks.meeting_base("회의록으로 정리해 줘", MEMO, TODAY, nearest=True) == (
        dt.date(2026, 10, 19), "near")
    korean = "10/14 교육혁신팀 주간회의. 참석 김지훈. 다음 회의 10/21(화) 10시 3층 회의실."
    assert report_checks.meeting_date("회의록", korean, TODAY) is None


def test_a_deadline_the_english_memo_gives_is_known():
    assert report_checks._day_known("2026-10-28(수)", MEMO)
    assert report_checks._day_known("10월 30일", MEMO)
    assert not report_checks._day_known("11월 2일", MEMO)


def test_an_english_weekday_backs_a_korean_one():
    memo = MEMO.replace("Oct 26 (Mon) 3pm, Room 4B", "Oct 26 (Mon) 3pm in Room 4B")
    assert stated_days.untraced("다음 회의는 월요일 오후 3시 Room 4B에서 열립니다.", memo) == []


def test_reading_english_days_finishes_on_a_long_run_of_spaces():
    started = time.perf_counter()
    weekdays.english_days("Oct" + " " * 50_000 + "19 (" + " " * 50_000 + "Mon")
    assert time.perf_counter() - started < 0.5
