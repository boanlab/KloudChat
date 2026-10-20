"""Minutes whose year question was skipped: the meeting day in the nearest year, deadlines
counted from it, the person's weekday kept and flagged."""

from __future__ import annotations

import datetime as dt

from app.services import report_checks, weekdays

#: the round-12 agent memo (one paragraph, typed as the request)
MEMO = ("10/14 교육혁신팀 주간회의. 참석 김지훈(팀장), 이수연, 박민호, 최다은. 불참 정우성(출장). "
        "안건1 2학기 튜터링 모집: 이수연이 포스터 시안을 다음 주 금요일까지 준비하기로 함, "
        "모집 기간은 11/3~11/14로 확정. 안건2 예산: 박민호가 간식비 80만원을 요청했으나 팀장이 "
        "50만원으로 줄이자고 했고 합의. 다음 회의 10/21 화 10시 3층 회의실.")
TODAY = dt.date(2026, 10, 20)


async def _dated(request: str, material: str) -> report_checks.Prepass:
    pre = report_checks.Prepass(format_id="minutes")
    job = report_checks.PrepassJob("minutes", request, material, None, "", "", TODAY, "", {})
    await report_checks._date_calc(pre, job)
    return pre


async def test_a_skipped_year_question_dates_the_meeting_in_the_nearest_year():
    # only the 원 sentence is read out as material; the rest of the memo is the request
    pre = await _dated(MEMO, "# 요청에 적은 자료\n안건2 예산: 박민호가 간식비 80만원을 요청했으나 "
                             "팀장이 50만원으로 줄이자고 했고 합의.")
    assert (pre.base_date, pre.base_source) == ("2026-10-14", "near")
    days = {r.get("kind"): r.get("date") for r in pre.dates}
    assert days["week_day"] == "2026-10-23"  # 다음 주 금요일 from Wednesday 10/14, never 10/17
    head = {"format": "minutes", "fields": [["회의명", "교육혁신팀 주간회의"], ["일시", ""]]}
    assert report_checks.minutes_head(head, pre, TODAY)["fields"][1] == ["일시", "2026-10-14(수)"]
    # the question itself still asks: the gap check reads no guessed year
    assert report_checks.meeting_base(MEMO, "", TODAY) == (None, "")
    # the next meeting's day is never the meeting's own
    later = "다음 회의 10/21 화 10시 회의실."
    assert report_checks.meeting_base(later, "", TODAY, nearest=True) == (None, "")


async def test_a_memo_typed_as_the_request_is_read_for_dates():
    pre = await _dated(MEMO, MEMO)
    assert any(r.get("date") == "2026-10-23" for r in pre.dates)


def test_the_persons_weekday_left_out_by_the_writer_is_put_back_flagged_once():
    text = "다음 회의는 10/21 10시에 3층 회의실에서 열립니다. 10/21 회의에서 비교표를 봅니다."
    fixed, fixes = weekdays.fix_weekdays(text, context=MEMO, today=TODAY)
    assert fixed == ("다음 회의는 10/21(화, 확인 필요: 달력상 수요일) 10시에 3층 회의실에서 "
                     "열립니다. 10/21 회의에서 비교표를 봅니다.")
    assert len(fixes) == 1
    # already flagged: left as it is
    again, more = weekdays.fix_weekdays(fixed, context=MEMO, today=TODAY)
    assert again == fixed and not more
