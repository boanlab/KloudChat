"""A one-line memo's meeting day is the day in the clause naming the meeting (「10/20 물류운영팀
주간회의」), not a deadline's (「10/23(금)까지」), and a decision the memo states, told as decided
(「…축소하기로 정했습니다」), is no task given to no one. Shape from the round 16 audit (H1)."""

from __future__ import annotations

import datetime as dt
import time

from app.services import report_checks, stated_meeting

MEMO = ("10/20 물류운영팀 주간회의. 참석 한도윤(팀장), 서지민, 오태경, 문가람. 불참 윤채린(연차). "
        "안건1 연말 성수기 임시 인력: 지난주 회의에서는 단기 아르바이트 12명을 뽑기로 했는데, "
        "인력업체 견적이 1인 일당 13만원으로 예상보다 높아서 8명으로 줄이고 모자라는 몫은 기존 "
        "인원 야간 연장근무로 메우기로 바꿈. 서지민이 8명 기준 재견적을 10/23(금)까지 받아 오기로 "
        "함. 안건2 반품 검수 구역: 2동 B구역으로 옮기는 것으로 확정, 이전 작업은 11/2(월)부터, "
        "오태경 담당. 안건3 지게차 1대 교체: 리스로 할지 구매로 할지 결론 못 냄, 문가람이 두 안 "
        "5년 비용 비교를 다음 회의에 가져오기로. 다음 회의 10/27(화) 오후 2시 2동 회의실.")
TODAY = dt.date(2026, 10, 23)


def test_the_meeting_day_is_not_a_deadline_on_the_memo_line():
    assert report_checks.meeting_date(MEMO, "", TODAY) == dt.date(2026, 10, 20)
    assert report_checks.meeting_base(MEMO, "", TODAY) == (dt.date(2026, 10, 20), "text")
    # a deadline in the meeting's own clause is still no meeting day
    said = "주간회의에서 재견적은 10/23(금)까지 받기로 함."
    assert report_checks.meeting_date(said, "", TODAY) is None


def test_a_meeting_day_with_its_weekday_still_comes_first():
    memo = "10/20(화) 주간회의. 재견적은 10/23(금)까지."
    assert report_checks.meeting_date(memo, "", TODAY) == dt.date(2026, 10, 20)


def test_a_decision_the_memo_states_is_no_unowned_task():
    told = ("첫째, 연말 성수기 임시 인력 규모를 기존 단기 아르바이트 12명에서 8명으로 "
            "축소하기로 정했습니다.")
    assert not stated_meeting.unowned_task(told, MEMO)
    # an act ahead for no one that the memo never gives is still one
    made = "첫째, 아르바이트 인원의 야간 수당과 교통비 지원 기준을 새로 세우기로 정했습니다."
    assert stated_meeting.unowned_task(made, MEMO)


def test_fifty_thousand_spaces_finish_quickly():
    gap = " " * 50_000
    started = time.perf_counter()
    report_checks.meeting_date(f"10/20{gap}주간회의{gap}10/23(금){gap}까지", "", TODAY)
    stated_meeting.unowned_task(f"축소하기로{gap}정했습니다.", MEMO)
    assert time.perf_counter() - started < 0.5
