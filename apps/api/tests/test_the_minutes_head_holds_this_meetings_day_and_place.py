"""Minutes head: the next meeting's line fills no field, a yearless day gets its year."""

from __future__ import annotations

import datetime as dt

from app.services import doc_formats, report_checks

#: the round-11 agent memo, one line
MEMO = ("10/14 교육혁신팀 주간회의. 참석 김지훈(팀장), 이수연, 박민호, 최다은. 불참 정우성(출장). "
        "안건1 2학기 튜터링 모집: 모집 기간은 11/3~11/14로 확정. 안건3 설문 도구: 결정 못 함, "
        "다음 회의에서 최다은이 비교표 가져오기. 다음 회의 10/21 화 10시 3층 회의실.")
TODAY = dt.date(2026, 10, 20)


def _fields(block: dict) -> dict[str, str]:
    return dict(map(tuple, block["fields"]))


def test_the_next_meetings_room_and_day_are_not_this_meetings():
    minutes = doc_formats.BY_ID["minutes"]
    answer = {"fields": {"회의명": "교육혁신팀 10/14 주간회의", "일시": "10/21 화 10시",
                         "장소": "10/21 화 10시 3층 회의실", "참석자": "김지훈(팀장), 이수연"}}
    fields = _fields(doc_formats.title_block(minutes, answer, MEMO))
    assert fields["장소"] == "" and fields["일시"] == ""
    assert fields["회의명"] == "교육혁신팀 10/14 주간회의"
    # this meeting's own day and place stay
    own = MEMO.replace("주간회의.", "주간회의, 본관 2층 회의실.", 1)
    kept = _fields(doc_formats.title_block(
        minutes, {"fields": {"일시": "10/14", "장소": "본관 2층 회의실"}}, own))
    assert kept["장소"] == "본관 2층 회의실" and kept["일시"] == "10/14"


def test_a_yearless_head_day_gets_the_year_nearest_today():
    head = {"format": "minutes", "fields": [["회의명", "주간회의"], ["일시", "10/14"]]}
    pre = report_checks.Prepass(format_id="minutes")
    assert report_checks.minutes_head(head, pre, TODAY)["fields"][1] == ["일시", "2026-10-14(수)"]
    timed = {**head, "fields": [["회의명", "x"], ["일시", "10월 14일 10시"]]}
    assert report_checks.minutes_head(timed, pre, TODAY)["fields"][1] == [
        "일시", "2026-10-14(수) 10시"]
    # the weekday the person wrote stays, flagged
    said = {**head, "fields": [["회의명", "x"], ["일시", "10/14 화 10시"]]}
    assert report_checks.minutes_head(said, pre, TODAY)["fields"][1] == [
        "일시", "2026-10-14(화, 확인 필요: 달력상 수요일) 10시"]
    # far from today: the year is the person's to fill
    assert report_checks.minutes_head(head, pre, dt.date(2026, 4, 14))["fields"][1] == [
        "일시", "[연도] 10월 14일"]
    # a full date is left alone
    full = {**head, "fields": [["회의명", "x"], ["일시", "2025-10-14 10:00"]]}
    assert report_checks.minutes_head(full, pre, TODAY) is None
