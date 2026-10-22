"""Minutes cut an agreement word without cutting the list it heads, give no memo person a task
the memo never gives them, keep no note on how the writer worked out the next meeting, and
count what the list after a count holds. Shapes from the
round 16 re-verify (reverify-x tc, tc2)."""

from __future__ import annotations

import time

from test_minutes_say_only_what_the_memo_says import TUTOR

from app.services import meeting_memo, report_checks, stated_meeting

ACTIONS = ("| 항목 | 담당 | 기한 | 비고 |\n| --- | --- | --- | --- |\n"
           "| 튜터링 포스터 시안 준비 | 이수연 | 2026-10-23(금) | - |\n"
           "| 간식비 50만원 집행 계획 수립 | {owner} | 미정 | 회의에서 정하지 않음 |\n"
           "| 설문 도구 비교표 작성 | 최다은 | 다음 회의 | - |")


def _sec(heading: str, content: str) -> dict:
    return {"heading": heading, "content": content}


def test_an_agreement_word_heading_a_list_goes_and_the_list_stays():
    listed = ("회의에서 합의한 업무는 포스터 시안 준비와 간식비 집행, 그리고 설문 도구 비교표 "
              "제작 3건입니다.")
    out = report_checks.settle_unsourced([_sec("실행 계획", listed)], TUTOR, strong=False)
    assert out[0]["content"] == ("회의에서 정한 업무는 포스터 시안 준비와 간식비 집행, 그리고 "
                                 "설문 도구 비교표 제작 3건입니다.")
    # a clause of its own still goes, the sentence ended again
    told = "설문 도구는 결정하지 못했으며, 참석자 전원이 이에 동의했습니다."
    assert stated_meeting.consent_cut(told, "참석자 전원이 이에 동의했습니다") \
        == "설문 도구는 결정하지 못했습니다."


def test_a_memo_person_gets_no_task_the_memo_never_gives_them():
    made = "박민호가 50만원 범위 내에서 지출 계획을 세우고 집행합니다."
    nobody = ("모집 기간은 이미 2026-11-03부터 2026-11-14까지 확정되어 있으므로, 포스터에 해당 "
              "일정을 포함해 제작합니다.")
    assert stated_meeting.unowned_task(made, TUTOR)
    assert stated_meeting.unowned_task(nobody, TUTOR)
    # the memo's tasks and the meeting told stand
    for said in ("이수연은 포스터 시안을 준비하고, 최다은은 설문 도구 비교표를 작성하며, 나머지 "
                 "항목은 확인이 필요합니다(표 2).",
                 "최다은이 구글폼과 네이버폼의 비교표를 만들어 다음 회의에 가져옵니다.",
                 "간식비 예산은 박민호가 80만원을 요청했으나 김지훈 팀장이 50만원으로 조정할 것을 "
                 "제안하여 50만원으로 합의했습니다.",
                 "김지훈 팀장, 이수연, 박민호, 최다은이 참석했고 정우성은 출장으로 불참했습니다.",
                 "이수연이 다음 주 금요일까지 포스터 시안을 준비합니다."):
        assert not stated_meeting.unowned_task(said, TUTOR), said
    decided = _sec("결정 사항", (
        "모집 기간은 2026-11-03부터 2026-11-14까지입니다. 간식비는 박민호의 80만원 요청 후 "
        "50만원으로 합의했습니다. 이수연이 포스터 시안을 준비합니다."))
    sections = [decided, _sec("실행 계획", f"{made} 최다은이 비교표를 가져옵니다.\n\n{nobody}")]
    # the names and figures they hold stand elsewhere: they go from a self-hosted writer's text
    weak = report_checks.settle_unsourced(sections, TUTOR, strong=False)
    assert weak[1]["content"] == "최다은이 비교표를 가져옵니다."
    assert weak[0] == decided
    # a strong writer's are marked where they stand
    strong = report_checks.settle_unsourced(sections, TUTOR, strong=True)
    assert "집행합니다 (확인 필요)." in strong[1]["content"]


def test_an_unowned_row_whose_task_no_memo_clause_gives_is_unassigned():
    # with no owner, a task no memo clause gives anyone goes as unassigned
    rows = report_checks.unassigned_rows([_sec("실행 계획", ACTIONS.format(owner="미정"))], TUTOR)
    assert [task for _, _, task in rows] == [["간식비 50만원 집행 계획 수립"]]
    # a memo task left without its owner is still a memo task
    poster = ACTIONS.format(owner="미정").replace("| 이수연 |", "| 미정 |")
    rows = report_checks.unassigned_rows([_sec("실행 계획", poster)], TUTOR)
    assert ["튜터링 포스터 시안 준비"] not in [task for _, _, task in rows]


def test_how_the_writer_worked_out_the_next_meeting_is_no_part_of_it():
    worked = ("회의 개요와 실행 계획 단계에서 확인한 바와 같이, 다음 회의 예정 일시는 "
              "2026-10-21(10/21 화)로 계산되었습니다.")
    source = ("그러나 원본 회의 기록에서는 '화'요일로 명시되어 있으므로, 실제 요일은 확인이 "
              "필요합니다(표 4).")
    for note in (worked, source):
        assert stated_meeting.self_note(note, TUTOR), note
    # a field the memo does not give is another check's
    assert not stated_meeting.self_note("3번의 기한은 회의 자료에 명시되지 않았습니다.", TUTOR)
    assert not stated_meeting.self_note("최다은이 비교표를 가져오기로 했습니다.", TUTOR)
    content = (f"최다은이 비교표를 가져오기로 했습니다.\n\n{worked} {source}\n\n"
               "**표 4. 다음 회의 정보**\n\n| 항목 | 내용 |\n| --- | --- |\n| 장소 | 3층 회의실 |")
    out = report_checks.settle_unsourced([_sec("다음 회의", content)], TUTOR, strong=False)
    text = out[0]["content"]
    assert "계산되었습니다" not in text and "원본" not in text
    assert text.startswith("최다은이 비교표를 가져오기로 했습니다(표 4).")


def test_a_count_says_how_many_the_list_after_it_holds():
    lead = ("회의에서 분담한 일은 세 가지입니다. 이수연은 포스터 시안을 준비하고, 최다은은 "
            "비교표를 작성합니다(표 2).")
    table = ("**표 2. 행동 항목**\n\n| 항목 | 담당 |\n|---|---|\n| 포스터 시안 준비 | 이수연 |\n"
             "| 비교표 작성 | 최다은 |")
    out, fixed = meeting_memo.counted([_sec("행동 항목", f"{lead}\n\n{table}")])
    assert fixed == 1
    assert out[0]["content"].startswith("회의에서 분담한 일은 두 가지입니다.")
    assert out[0]["content"].endswith(table)
    listed = "후속 과제는 다음 3건입니다.\n\n- 포스터 시안\n- 비교표"
    assert meeting_memo.counted([_sec("후속", listed)])[0][0]["content"].startswith(
        "후속 과제는 다음 2건입니다.")
    # a count that agrees, or stands apart from the list, stays
    same = f"{lead.replace('세 가지', '두 가지')}\n\n{table}"
    assert meeting_memo.counted([_sec("행동 항목", same)]) == ([_sec("행동 항목", same)], 0)
    apart = f"{lead}\n\n설문 도구는 다음 회의에서 정합니다.\n\n{table}"
    assert meeting_memo.counted([_sec("행동 항목", apart)])[1] == 0


def test_a_cell_holds_no_note_on_how_the_writer_got_its_value():
    table = ("| 항목 | 내용 | 비고 |\n| --- | --- | --- |\n"
             "| 포스터 시안 | 2026-10-23(금) | 회의 발언: '다음 주 금요일까지' "
             "(기준 2026-10-14) |\n"
             "| 장소 | 3층 회의실 | 회의 자료에서 명시 |\n"
             "| 모집 기간 | 2026-11-03 ~ 2026-11-14 | 회의에서 확정 |")
    out, fixed = meeting_memo.cell_notes([_sec("다음 회의", table)], TUTOR)
    text = out[0]["content"]
    assert fixed == 2
    assert "| 회의 발언: '다음 주 금요일까지' |" in text and "(기준" not in text
    assert "| 장소 | 3층 회의실 | - |" in text
    # what the meeting did stays
    assert "| 회의에서 확정 |" in text


def test_the_new_checks_finish_on_a_long_run_of_spaces():
    gap = " " * 50_000
    start = time.perf_counter()
    stated_meeting.self_note(f"원본{gap}에서는 명시되어", TUTOR)
    stated_meeting.unowned_task(f"박민호가{gap}집행합니다.", TUTOR)
    stated_meeting.consent_cut(f"합의한{gap}업무, 그리고 3건입니다.", f"합의한{gap}업무")
    meeting_memo.counted([_sec("행동", f"다음{gap}3건입니다.\n\n- a\n- b")])
    row = f"| 항목 | 비고 |\n|---|---|\n| a | (기준{gap}2026) 회의{gap}자료에서{gap}명시 |"
    meeting_memo.cell_notes([_sec("행동", row)], TUTOR)
    assert time.perf_counter() - start < 0.5
