"""Minutes keep this meeting apart from the next one and from the writer's handling of the
text: a sentence saying what the writer kept out of the document or which year it took as its
basis goes, the next meeting's room or clock is no value of this meeting's overview or head,
and an agreement told of an item by pointing back (「이는 … 동의한 최종 합의 사항입니다」) is
judged by the sentence it points to. Shapes from the round 16 re-verify (tc, tc2)."""

from __future__ import annotations

import time

from test_minutes_say_only_what_the_memo_says import TUTOR

from app.services import meeting_memo, report_checks, stated_meeting

OVERVIEW = ("참석자는 김지훈(팀장), 이수연, 박민호, 최다은입니다. 회의 날짜는 연도가 기재되지 않아 "
            "대화 발생 시점과 가장 가까운 연도인 2026년을 기준으로 확정했습니다.\n\n"
            "안건으로 2학기 튜터링 모집, 예산, 설문 도구 논의를 다뤘습니다. 점심 메뉴와 주차 "
            "관련 대화가 간략히 있었으나, 이는 회의 안건과 무관하여 본문에 반영하지 "
            "않았습니다(표 1).\n\n"
            "**표 1. 회의 개요**\n\n| 항목 | 내용 |\n| :--- | :--- |\n| 일시 | 2026-10-14(수) |\n"
            "| 장소 | 3층 회의실 |\n\n확인 필요: 회의 장소는 자료에 기재되어 있지 않아 확인이 "
            "필요합니다.")
NEXT = "**표 4. 다음 회의**\n\n| 항목 | 내용 |\n| :--- | :--- |\n| 장소 | 3층 회의실 |"
DECIDED = ("안건 1의 2학기 튜터링 모집 기간에 대해 논의한 결과, 모집 기간을 11/3~11/14로 "
           "확정했습니다. 이는 모든 참석자가 동의한 최종 합의 사항입니다.\n\n"
           "안건 2의 간식비 예산은 팀장의 의견에 따라 50만원으로 합의했습니다. 참석자 전원이 "
           "이에 동의했습니다.")


def _sec(heading: str, content: str) -> dict:
    return {"heading": heading, "content": content}


def test_what_the_writer_kept_out_of_the_text_and_the_year_it_took_go():
    left = ("점심 메뉴와 주차 관련 대화가 간략히 있었으나, 이는 회의 안건과 무관하여 본문에 "
            "반영하지 않았습니다(표 1).")
    year = ("회의 날짜는 연도가 기재되지 않아 대화 발생 시점과 가장 가까운 연도인 2026년을 "
            "기준으로 확정했습니다.")
    assert stated_meeting.self_note(left, TUTOR)
    assert stated_meeting.self_note(year, TUTOR)
    assert stated_meeting.self_note("해당 비교표의 내용은 이번 회의록에는 포함되어 있지 "
                                    "않습니다.", TUTOR)
    # the meeting's own act stays
    assert not stated_meeting.self_note("모집 기간을 11/3부터 11/14까지로 확정했습니다.", TUTOR)
    assert not stated_meeting.self_note("설문 도구는 결정하지 않았습니다.", TUTOR)
    assert not stated_meeting.self_note("후속 조치는 아래 표에 정리했습니다(표 3).", TUTOR)
    out = report_checks.settle_unsourced([_sec("회의 개요", OVERVIEW)], TUTOR, strong=False)
    text = out[0]["content"]
    assert "반영하지 않았" not in text and "기준으로 확정" not in text
    # the table pointer the dropped sentence held stays on the sentence before
    assert "설문 도구 논의를 다뤘습니다(표 1)." in text


def test_the_next_meeting_s_room_is_no_value_of_this_meeting():
    sections = [_sec("회의 개요", OVERVIEW), _sec("다음 회의", NEXT)]
    out, fixed = meeting_memo.borrowed_next(sections, TUTOR)
    assert fixed == 1
    assert "| 장소 | 미정 |" in out[0]["content"]
    # the line saying the place is not given now agrees with the cell
    assert "확인 필요: 회의 장소는" in out[0]["content"]
    assert out[1] == sections[1]
    held = [_sec("회의 개요", "회의는 3층 회의실에서 열렸습니다. 참석자는 네 명입니다.")]
    assert meeting_memo.borrowed_next(held, TUTOR)[0][0]["content"] == "참석자는 네 명입니다."
    # a memo that gives this meeting's room keeps it
    own = TUTOR.replace("주간회의.", "주간회의, 장소 3층 회의실.", 1)
    assert meeting_memo.borrowed_next(sections, own) == (sections, 0)


def test_the_head_takes_no_room_or_clock_of_the_next_meeting():
    block = {"fields": [["일시", "2026-10-14(수) 10:00"], ["장소", "3층 회의실"]]}
    fields = dict(map(tuple, meeting_memo.fill_head(block, TUTOR)["fields"]))
    assert fields == {"일시": "2026-10-14(수)", "장소": "[장소]"}


def test_an_agreement_pointing_back_is_judged_by_the_sentence_it_points_to():
    found = stated_meeting.findings([_sec("결정 사항", DECIDED)], TUTOR)
    said = [p for _, _, phrases in found for p in phrases]
    assert "이는 모든 참석자가 동의한 최종 합의 사항입니다" in said
    # the memo states the 간식비 agreement
    assert not any("전원" in p for p in said)
    out = report_checks.settle_unsourced([_sec("결정 사항", DECIDED)], TUTOR, strong=False)
    assert "모든 참석자가 동의" not in out[0]["content"]
    assert "11/3~11/14로 확정했습니다." in out[0]["content"]


def test_the_checks_finish_on_a_long_run_of_spaces():
    text = "본문에" + " " * 50_000 + "반영하지 않았습니다."
    start = time.perf_counter()
    stated_meeting.self_note(text, TUTOR)
    row = "| 장소 |" + " " * 50_000 + "3층 회의실 |"
    meeting_memo.borrowed_next([_sec("회의 개요", row)], TUTOR)
    meeting_memo.fill_head({"fields": [["장소", " " * 50_000 + "3층 회의실"]]}, TUTOR)
    assert time.perf_counter() - start < 0.5
