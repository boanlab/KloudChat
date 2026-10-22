"""Minutes keep the memo through card answers and repairs: the 「덧붙인 조건」 block the card
adds is not the meeting, a repair is asked for the sentence alone, a note on what the writer
left out goes though another check marked its sentence, an agreement the
memo never states is cut or marked, a deadline the memo gives as 「다음 회의에서」 is not called
missing, and a row written twice goes. Shapes from the round 16 re-verify (tc, tc2)."""

from __future__ import annotations

import time

from test_minutes_say_only_what_the_memo_says import SALES, TUTOR

from app.pipeline.bundle import Bundle, Material, Request, Spec
from app.pipeline.report_stages import _doc
from app.services import (
    meeting_memo,
    process_settings,
    report,
    report_checks,
    stated_meeting,
    verify,
)

#: the request as the card's 「있는 자료로 진행」 sent it
CARDED = (TUTOR + "\n\n덧붙인 조건:\n- 있는 자료로 진행해 주세요\n- 사용자가 더 줄 정보가 없다고 "
          "했다. 다음은 지어내지 않는다: 회의 일시 → [회의 일자]. 그 대괄호는 사용자가 직접 채울 "
          "머리 칸(일시·작성자 등)·서명·연락처에만 두고, 본문에서는 그 값 없이도 맞는 문장으로 "
          "쓴다(표 칸은 「미정」, 표 아래 「확인 필요」 한 줄).")
NEXT = ("다음 회의는 2026년 10월 21일 오전 10시에 3층 회의실에서 열립니다.\n\n"
        "**표 5. 다음 회의**\n\n| 항목 | 내용 |\n| --- | --- |\n"
        "| 일시 | 2026년 10월 21일(화, 확인 필요: 달력상 수요일) 오전 10시 |\n"
        "| 장소 | 3층 회의실 |")
ACTIONS = ("실행 항목은 표 2와 같습니다.\n\n**표 2. 실행 항목**\n\n| 할 일 | 담당 | 기한 | 비고 |\n"
           "| :--- | :--- | :--- | :--- |\n"
           "| 포스터 시안 준비 | 이수연 | 2026-10-23(금) | 원 발언: 다음 주 금요일까지 |\n"
           "| 설문 도구 비교표 준비 | 최다은 | 미정 | 「다음 회의에서」. 구글폼과 네이버폼 비교 |\n"
           "| 설문 도구 비교표 준비 | 최다은 | 미정 | 회의 발언「다음 회의에서」. "
           "구글폼과 네이버폼 비교 |\n\n설문 도구 비교표의 기한은 자료에 명시된 기한이 없으므로 "
           "확인 필요. 포스터는 이수연이 준비합니다.\n\n"
           "확인 필요: 설문 도구 비교표의 명확한 제출 기한(다음 회의 당일 또는 회의 전)은 "
           "회의 자료에 명시되어 있지 않아 확인이 필요합니다.")


def _sec(heading: str, content: str) -> dict:
    return {"heading": heading, "content": content}


def _minutes_bundle(text: str) -> Bundle:
    given = report._request_material(text)
    return Bundle(request=Request(text=text, model="local/m", api_key="k"),
                  spec=Spec(format_id="minutes", process=process_settings.for_format("minutes")),
                  material=Material(given=given))


def test_the_card_s_conditions_are_no_pasted_material_and_the_memo_stays_the_meeting():
    # the block alone once read as the pasted material, the memo above it lost
    assert "덧붙인 조건" not in report._request_material(CARDED)
    memo = meeting_memo.of_request(CARDED)
    assert memo.startswith(TUTOR) and "있는 자료로 진행해 주세요" in memo
    assert "지어내지 않는다" not in memo and "그 값 없이도" not in memo
    # an answer to the card is the meeting's
    answered = TUTOR + "\n\n덧붙인 조건:\n- 장소: 본관 302호"
    assert meeting_memo.of_request(answered).endswith("장소: 본관 302호")
    doc = _doc(_minutes_bundle(CARDED))
    assert doc.memo.startswith(TUTOR) and doc.given.startswith(TUTOR)
    assert "지어내지 않는다" not in doc.memo


def test_with_the_memo_kept_an_hour_part_it_never_gives_goes():
    doc = _doc(_minutes_bundle(CARDED))
    out = report_checks.settle_unsourced([_sec("다음 회의", NEXT)], doc.memo, strong=False)
    assert "오전" not in out[0]["content"]
    assert "2026년 10월 21일 10시에 3층 회의실" in out[0]["content"]


def test_the_repair_asks_for_the_sentence_alone():
    # a reply is the sentence as it will stand, never an account of the edit
    assert "no\n  note on what you changed, left out or why" in verify._REPAIR_PROMPT


def test_a_note_on_what_the_writer_left_out_goes_though_another_check_marked_it():
    overview = ("회의록 작성자는 교육혁신팀 김에이전트입니다.\n\n"
                "점심 메뉴와 주차 이야기는 회의 안건과 무관하여 본문에 반영하지 않았습니다 "
                "(확인 필요).")
    for strong in (False, True):
        left = report_checks.settle_unsourced([_sec("회의 개요", overview)], TUTOR,
                                              strong=strong)[0]["content"]
        assert left == "회의록 작성자는 교육혁신팀 김에이전트입니다."


def test_an_agreement_the_memo_never_states_is_cut_by_code_or_marked_for_a_strong_writer():
    talk = ("모집 기간은 시작일과 종료일이 함께 거론되었으며, 참석자들이 이에 동의하였습니다. "
            "간식비는 50만원으로 줄이는 방향에 합의하였고, 참석자들이 이에 동의하였습니다.")
    decided = ("첫째, 모집 기간을 11월 3일부터 11월 14일까지로 확정했습니다. 이는 이수연의 제안에 "
               "따라 팀 전체가 동의한 사안입니다.\n\n**표 1. 결정 사항**\n\n"
               "| 항목 | 내용 | 비고 |\n"
               "| --- | --- | --- |\n| 튜터링 모집 기간 | 2026-11-03 ~ 2026-11-14 | 이수연 제안, "
               "전원 동의 |")
    sections = [_sec("결정 사항", decided), _sec("토의 내용", talk)]
    out = report_checks.settle_unsourced(sections, TUTOR, strong=False)
    first, second = out[0]["content"], out[1]["content"]
    assert "모집 기간은 시작일과 종료일이 함께 거론되었습니다." in second
    # the memo's 합의 on the budget: its agreement stands
    assert "간식비는 50만원으로 줄이는 방향에 합의하였고, 참석자들이 이에 동의하였습니다." in second
    assert "팀 전체가 동의" not in first and "| 이수연 제안 |" in first
    marked = report_checks.settle_unsourced(sections, TUTOR, strong=True)
    assert "참석자들이 이에 동의하였습니다(확인 필요)." in marked[1]["content"]
    assert "전원 동의(확인 필요)" in marked[0]["content"]


def test_a_deadline_the_memo_gives_as_the_next_meeting_is_not_called_missing():
    assert ("다음 회의", {"비교표", "가져오기"}) in [
        (p, w) for p, w in meeting_memo.relative_dues(TUTOR)]
    # the next meeting's own dated clause is no task's
    assert all(p != "다음 회의" or "회의실" not in w for p, w in meeting_memo.relative_dues(TUTOR))
    out, fixed = meeting_memo.false_dues([_sec("실행 항목", ACTIONS)], TUTOR)
    text = out[0]["content"]
    assert fixed == 4
    assert "| 설문 도구 비교표 준비 | 최다은 | 다음 회의 | 「다음 회의에서」." in text
    assert "명시된 기한이 없으므로" not in text and "명시되어 있지 않아" not in text
    assert text.rstrip().endswith("포스터는 이수연이 준비합니다.")
    # a task with no deadline in the memo keeps its claim
    undated = SALES.replace("이번 주 안에", "")
    kept, n = meeting_memo.false_dues([_sec("실행 항목", ACTIONS)], undated)
    assert n == 0 and kept[0]["content"] == ACTIONS


def test_a_row_written_twice_goes_and_the_fuller_one_stays():
    out, gone = meeting_memo.dedupe_rows([_sec("실행 항목", ACTIONS)])
    text = out[0]["content"]
    assert gone == 1 and text.count("| 설문 도구 비교표 준비 |") == 1
    assert "| 설문 도구 비교표 준비 | 최다은 | 미정 | 회의 발언「다음 회의에서」." in text
    # rows that differ in what they say both stay
    two = ("| 할 일 | 담당 | 기한 |\n| --- | --- | --- |\n| 포스터 | 이수연 | 10/23 |\n"
           "| 비교표 | 최다은 | 10/21 |")
    assert meeting_memo.dedupe_rows([_sec("실행 항목", two)])[1] == 0


def test_the_new_patterns_finish_on_long_spaces():
    spaces = " " * 50_000
    start = time.perf_counter()
    meeting_memo.of_request(f"메모{spaces}\n\n덧붙인 조건:\n- {spaces}쓴다")
    meeting_memo.false_dues([_sec("실행 항목", f"기한{spaces}없음")], TUTOR)
    stated_meeting.untraced(f"기준일{spaces}을 바탕으로 동의{spaces}, 합의{spaces}.", TUTOR)
    assert time.perf_counter() - start < 0.5
