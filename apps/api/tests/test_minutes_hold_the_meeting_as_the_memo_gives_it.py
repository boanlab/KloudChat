"""Minutes hold the meeting as the memo gives it: no day, clock or place called missing that
the memo gives, no empty head field it fills, and no note on its own writing the memo never
gives. Shapes from the round 16 re-verify (tc, tc2, fpm)."""

from __future__ import annotations

import time

from test_minutes_say_only_what_the_memo_says import SALES, TUTOR

from app.services import meeting_memo, report_checks, stated_meeting

NEXT = ("자료에 나와 있는 내용은 날짜뿐이며(표 5).\n\n**표 5. 다음 회의**\n\n| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 일시 | 2026-10-21(화, 확인 필요: 달력상 수요일) (시간은 자료에 없음) |\n"
        "| 장소 | 미정 |\n| 예정 안건 | 미정 |")
OVERVIEW = ("2026-10-14(수)에 교육혁신팀 주간회의가 열렸습니다. "
            "회의는 세 가지 안건으로 진행했습니다(표 1)."
            "\n\n**표 1. 회의 개요**\n\n| 항목 | 내용 |\n|------|------|\n"
            "| 일시 | 2026-10-21(화, 확인 필요: 달력상 수요일) (시간은 자료에 없음) |\n"
            "| 참석 | 김지훈(팀장), 이수연, 박민호, 최다은 |\n\n"
            "확인 필요: 일시와 장소는 회의 자료에 나오지 않아 확인이 필요합니다.")
HEAD = {"format": "minutes", "fields": [["회의명", ""], ["일시", "2026-10-14(수)"], ["장소", ""],
                                        ["참석자", "김지훈(팀장), 이수연, 박민호, 최다은"]]}


def _sec(heading: str, content: str) -> dict:
    return {"heading": heading, "content": content}


def test_the_memo_gives_this_meetings_head_and_the_next_meetings_clause():
    tutor = meeting_memo.read(TUTOR)
    assert (tutor.this.name, tutor.this.day, tutor.this.clock, tutor.this.place) == (
        "교육혁신팀 주간회의", "10/14", "", "")
    assert tutor.this.attendees == "김지훈(팀장), 이수연, 박민호, 최다은"
    assert (tutor.next.day, tutor.next.clock, tutor.next.place) == (
        "10/21 화", "10시", "3층 회의실")
    sales = meeting_memo.read(SALES)
    assert (sales.this.name, sales.this.clock, sales.this.place) == (
        "영업1팀 주간회의", "오후 2시", "본사 5층 대회의실")
    assert (sales.next.day, sales.next.place) == ("10월 28일(수)", "본사 5층 대회의실")


def test_a_clock_or_place_called_missing_that_the_memo_gives_is_filled_or_the_claim_goes():
    out, fixed = meeting_memo.false_absences([_sec("다음 회의", NEXT)], TUTOR)
    text = out[0]["content"]
    assert fixed == 3
    assert "| 일시 | 2026-10-21(화, 확인 필요: 달력상 수요일) 10시 |" in text
    assert "| 장소 | 3층 회의실 |" in text and "| 예정 안건 | 미정 |" in text
    # the false 「날짜뿐」 goes, its table pointer kept
    assert "날짜뿐" not in text and text.startswith("정리하면 다음과 같습니다(표 5).")
    listed, _ = meeting_memo.false_absences(
        [_sec("다음 회의", "- 장소: 미정\n- 시간: [시간]")], TUTOR)
    assert listed[0]["content"] == "- 장소: 3층 회의실\n- 시간: 10시"
    # this meeting's clock and place the memo does not give: the claim is true and stays
    held = OVERVIEW.replace("일시와 장소는", "장소는")
    kept, n = meeting_memo.false_absences([_sec("회의 개요", held)], TUTOR)
    assert n == 0 and kept[0]["content"] == held
    # 「일시」 called missing while the memo dates the meeting: the sentence goes
    gone, n = meeting_memo.false_absences([_sec("회의 개요", OVERVIEW)], TUTOR)
    assert n == 1 and "나오지 않아" not in gone[0]["content"]
    assert "(시간은 자료에 없음)" in gone[0]["content"]


def test_an_empty_head_field_takes_the_memos_value_or_a_blank_to_fill():
    fields = dict(map(tuple, meeting_memo.fill_head(HEAD, TUTOR)["fields"]))
    assert fields["회의명"] == "교육혁신팀 주간회의"
    # the next meeting's room is no place for this one
    assert fields["장소"] == "[장소]"
    sales = {"format": "minutes", "fields": [["회의명", "영업1팀 주간회의"],
                                             ["일시", "2026-10-21(수)"], ["장소", ""]]}
    filled = dict(map(tuple, meeting_memo.fill_head(sales, SALES)["fields"]))
    assert filled["일시"] == "2026-10-21(수) 오후 2시" and filled["장소"] == "본사 5층 대회의실"
    full = {"format": "minutes", "fields": [["회의명", "x"], ["일시", "2026-10-21(수) 오후 2시"]]}
    assert meeting_memo.fill_head(full, SALES) is None


def test_the_memo_s_decisions_are_its_decided_clauses():
    assert [c for _, c in meeting_memo.decisions(TUTOR)] == [
        "모집 기간은 11/3~11/14로 확정",
        "박민호가 간식비 80만원을 요청했으나 팀장이 50만원으로 줄이자고 했고 합의"]
    # a memo with no decision (「결정 못 함」 alone) has none
    assert meeting_memo.decisions("안건3 설문 도구: 구글폼 vs 네이버폼 논의, 결정 못 함") == []


def test_a_note_on_the_writing_the_memo_never_gives_goes():
    note = "본 회의록에서 상대 날짜를 해석하는 기준일은 회의 일자 2026-10-14입니다."
    pointed = "「다음 회의」를 2026-10-21로 해석하여 기한에 반영했습니다(표 1)."
    lead = "회의는 세 가지 안건으로 진행했습니다."
    table = "**표 1. 회의 개요**\n\n| 항목 | 내용 |\n|---|---|\n| 참석 | 김지훈 |"
    content = f"{lead}\n\n{note} {pointed}\n\n{table}"
    for strong in (False, True):
        text = report_checks.settle_unsourced([_sec("회의 개요", content)], TUTOR,
                                              strong=strong)[0]["content"]
        assert "해석" not in text and text.startswith("회의는 세 가지 안건으로 진행했습니다(표 1).")


def test_the_memo_checks_finish_on_a_long_run_of_spaces():
    spaces = " " * 50_000
    started = time.perf_counter()
    meeting_memo.read("다음 회의" + spaces + "회의실" + spaces + "장소")
    meeting_memo.false_absences(
        [_sec("다음 회의", "장소" + spaces + "자료에" + spaces + "없음")], TUTOR)
    meeting_memo.decisions("안건: " + spaces + "확정" + spaces)
    stated_meeting.untraced("알" + spaces + "수 없" + spaces + "해석하는", SALES)
    assert time.perf_counter() - started < 0.5
