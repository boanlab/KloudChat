"""Minutes tell the meeting as it was: no guess, no next-meeting agenda, no missing-detail note
and no mail lines the memo never gives; the meeting held in the past tense; no prose calling a
deadline open that its action row dates from the memo."""

from __future__ import annotations

import datetime as dt
import time

from test_minutes_say_only_what_the_memo_says import SALES, TUTOR

from app.services import meeting_tense, report_checks, skill_scripts, stated_meeting
from app.services.verify import Doc

dates = skill_scripts.module("date-calc", "dates")

GUESS = ("다만, 2026-10-28(수)까지의 마감 기한이 있는 과제(한지민의 거래처 리스트, 류태호의 견적 "
         "양식 초안)가 당일 또는 그 직전에 점검되거나 보고될 가능성이 높습니다.")
AGENDA = "이번 회의의 실행 과제들도 진행 상황을 공유할 것입니다."
GAP = ("- 안건 1(4분기 신규 거래처 목표)에서 후보 리스트 10곳에 들어가는 곳과 선정 기준이 회의 "
       "자료에 제시되지 않았습니다.")


def test_a_guess_the_memo_never_makes_is_found_and_the_memos_own_doubt_is_not():
    assert stated_meeting.untraced(GUESS, SALES)
    assert stated_meeting.untraced("최다은의 비교표 제출 이후에야 선택이 가능할 것으로 보입니다.",
                                   TUTOR)
    assert stated_meeting.untraced("포스터 시안도 같은 날 검토될 수 있으나, 확정은 아닙니다.",
                                   TUTOR)
    # the memo's 「들음」: its doubt the minutes may carry
    heard = "어제 협력사 미팅: 납기 11월 말로 합의됐다고 들음. 단가는 아직 협의 중."
    assert not stated_meeting.untraced("납기는 11월 말로 합의된 것으로 보입니다.", heard)
    # what someone may do is no guess
    assert not stated_meeting.untraced("새 양식은 팀장 승인 후 사용할 수 있습니다.", SALES)


def test_a_next_meeting_agenda_the_memo_never_sets_is_found_only_in_that_part():
    sections = [
        {"heading": "토의 내용", "content": AGENDA},
        {"heading": "다음 회의 안내", "content": (
            "다음 회의는 2026년 10월 21일 오전 10시에 3층 회의실에서 열립니다. "
            f"예정된 주요 안건은 설문 도구 비교 검토입니다. {AGENDA}")},
    ]
    found = stated_meeting.findings(sections, TUTOR)
    assert [(i, p) for i, p, _ in found] == [(1, AGENDA)]
    # the memo's own next-meeting lines set nothing for 영업1팀: any agenda is the writer's
    assert stated_meeting.untraced("예정 안건은 견적 양식 초안 검토입니다.", SALES, next_part=True)
    assert not stated_meeting.untraced(
        "다음 회의는 2026년 10월 28일(수) 오후 2시에 본사 5층 대회의실에서 열립니다.", SALES,
        next_part=True)


def test_a_note_that_the_memo_lacks_a_detail_is_found_and_a_missing_field_is_not():
    assert stated_meeting.untraced(GAP, SALES)
    assert stated_meeting.untraced(
        "누락 건에 해당하는 날짜와 금액은 회의 자료에 포함되어 있지 않습니다.", SALES)
    for field in ("회의의 기준 일자는 자료에 연도가 명시되지 않아 2026년으로 두었습니다.",
                  "3번의 기한은 회의 자료에 명시되지 않았습니다."):
        assert not stated_meeting.untraced(field, TUTOR)


def test_mail_lines_in_minutes_are_found_unless_a_mail_was_asked_for():
    assert stated_meeting.untraced("제목: 영업1팀 주간회의 일정", SALES)
    assert stated_meeting.untraced(
        "본문: 이번 주 처리가 필요한 항목을 확정하기 위해 회의를 개최합니다.", SALES)
    assert not stated_meeting.untraced("제목: 영업1팀 주간회의 일정", SALES + " 공지 메일도 써 줘.")


SALES_MINUTES = [
    {"heading": "회의 개요", "content": (
        "2026년 10월 21일(수) 오후 2시 본사 5층 대회의실에서 영업1팀 주간회의가 열렸습니다. "
        "참석자는 오세훈(팀장), 한지민, 류태호, 서은우입니다.\n\n제목: 영업1팀 주간회의 일정\n\n"
        "본문: 이번 주 처리가 필요한 항목과 다음 회의 시점을 확정하기 위해 회의를 개최합니다.")},
    {"heading": "항목별 논의 내용", "content": (
        "한지민이 거래처 후보 리스트 10곳을 2026년 10월 23일(금)까지 정리하기로 했습니다. "
        "류태호가 견적 양식 초안을 10월 28일(수)까지 공유하기로 했습니다.")},
    {"heading": "확인 필요 사항", "content": (
        "- 3번의 기한: 회의에서는 「이번 주 안에」라고만 언급되었습니다.\n" + GAP)},
    {"heading": "다음 회의 일정", "content": (
        "다음 회의는 2026년 10월 28일(수) 오후 2시에 본사 5층 대회의실에서 열립니다. " + GUESS)},
]


def test_what_stands_after_the_repair_goes_when_its_names_stand_elsewhere():
    found = report_checks.minutes_unsourced(Doc(SALES_MINUTES, material=SALES))
    assert len(found) == 4 and all(f.still(f.sentence) for f in found)
    weak = report_checks.settle_unsourced(SALES_MINUTES, SALES, strong=False)
    assert "제목:" not in weak[0]["content"] and "본문:" not in weak[0]["content"]
    assert weak[0]["content"].endswith("서은우입니다.")
    assert weak[2]["content"] == "- 3번의 기한: 회의에서는 「이번 주 안에」라고만 언급되었습니다."
    assert weak[3]["content"] == (
        "다음 회의는 2026년 10월 28일(수) 오후 2시에 본사 5층 대회의실에서 열립니다.")
    # what the memo said stands untouched
    assert weak[1] == SALES_MINUTES[1]
    # a strong writer's are marked where they stand; mail lines go whoever wrote them
    strong = report_checks.settle_unsourced(SALES_MINUTES, SALES, strong=True)
    assert "제목:" not in strong[0]["content"]
    assert "제시되지 않았습니다 (확인 필요)." in strong[2]["content"]
    assert "보고될 가능성이 높습니다 (확인 필요)." in strong[3]["content"]


def test_a_guess_holding_names_said_nowhere_else_is_marked_not_taken_out():
    alone = [{"heading": "다음 회의", "content": GUESS}]
    out = report_checks.settle_unsourced(alone, SALES, strong=False)
    assert "가능성이 높습니다 (확인 필요)." in out[0]["content"]
    assert "한지민" in out[0]["content"]


ACTIONS = ("| 항목 | 할 일 | 담당 | 기한 | 비고 |\n|---|---|---|---|---|\n"
           "| 1 | 포스터 시안 준비 | 이수연 | 2026-10-23(금) | 원 표현: '다음 주 금요일' |\n"
           "| 2 | 설문 도구 비교표 준비 | 최다은 | 2026-10-21(수) | 원 표현: '다음 회의에서' |")


def test_prose_calling_a_deadline_open_takes_the_memos_date_from_its_row():
    found = dates.scan(TUTOR, dt.date(2026, 10, 14))
    sections = [
        {"heading": "안건별 논의 내용", "content": (
            "이수연이 포스터 시안을 준비하겠다고 밝혔으며, 시안 제출 기한은 미정입니다. "
            "예산 집행 기한은 정해지지 않았습니다.")},
        {"heading": "실행 과제", "content": ACTIONS},
    ]
    out, fixed = report_checks.minutes_open_due(sections, found)
    assert fixed == 1
    assert out[0]["content"] == ("이수연이 포스터 시안을 준비하겠다고 밝혔으며, 시안 제출 기한은 "
                                 "2026-10-23(금)입니다. 예산 집행 기한은 정해지지 않았습니다.")
    # a row date the memo never gives settles nothing
    made_up = ACTIONS.replace("2026-10-23(금)", "2026-10-30(금)")
    _, none = report_checks.minutes_open_due([sections[0], {"heading": "", "content": made_up}],
                                             found)
    assert none == 0


def test_the_meeting_held_is_told_in_the_past_and_what_comes_next_is_not():
    sections = [
        {"heading": "회의 개요", "content": (
            "2026년 10월 14일 교육혁신팀 주간회의가 열립니다. 정우성은 출장으로 불참합니다.이번 "
            "회의의 작성자는 김에이전트입니다.\n\n회의는 세 가지 안건을 다룹니다.")},
        {"heading": "토의 내용", "content": (
            "모집 기간이 11월 3일부터 11월 14일까지로 정해집니다. 참석자 전원이 합의합니다. "
            "설문 도구는 결론에 이르지 못합니다 (확인 필요). 류태호가 초안을 10월 28일까지 "
            "공유하기로 합니다. 팀장 승인 후 11월 1주차부터 새 양식이 적용됩니다. 설문 도구는 "
            "다음 회의에서 결정합니다.\n\n| 1 | 모집 기간을 확정합니다. |")},
        {"heading": "다음 회의", "content": "다음 회의는 10월 21일 10시에 진행됩니다."},
    ]
    out, changed = meeting_tense.held(sections)
    assert changed == 3
    assert out[0]["content"] == (
        "2026년 10월 14일 교육혁신팀 주간회의가 열렸습니다. 정우성은 출장으로 불참했습니다.이번 "
        "회의의 작성자는 김에이전트입니다.\n\n회의는 세 가지 안건을 다뤘습니다.")
    assert out[1]["content"] == (
        "모집 기간이 11월 3일부터 11월 14일까지로 정해졌습니다. 참석자 전원이 합의했습니다. "
        "설문 도구는 결론에 이르지 못했습니다 (확인 필요). 류태호가 초안을 10월 28일까지 "
        "공유하기로 했습니다. 팀장 승인 후 11월 1주차부터 새 양식이 적용됩니다. 설문 도구는 "
        "다음 회의에서 결정합니다.\n\n| 1 | 모집 기간을 확정합니다. |")
    assert out[2] == sections[2]
    # a word ending like an act is no act: 「규모입니다」
    assert meeting_tense.past("예산 규모입니다.") == "예산 규모입니다."


def test_the_new_minutes_checks_finish_on_a_long_run_of_spaces():
    spaces = " " * 50_000
    started = time.perf_counter()
    stated_meeting.untraced(f"자료에{spaces}제시{spaces}않", SALES, next_part=True)
    stated_meeting.untraced(f"제목{spaces}:", SALES)
    stated_meeting.untraced(f"가능성이{spaces}높 수{spaces}있을 것", SALES)
    meeting_tense.past(f"회의가 열립니다{spaces}(확인 필요){spaces}.")
    report_checks.minutes_open_due(
        [{"heading": "", "content": f"기한은{spaces}미정입니다.\n\n" + ACTIONS}],
        [{"date": "2026-10-23", "status": "ok"}])
    assert time.perf_counter() - started < 0.5


def test_a_persons_copy_of_the_minutes_template_keeps_its_minutes_steps():
    from app.services import design_templates, process_settings

    full = process_settings.resolve(template_id="doc-minutes", format_id="minutes")
    own = process_settings.of_template(design_templates.get("doc-minutes"))
    assert process_settings.only_for_format(full, "ut-copy", own) == full
    # a format the steps were not set for still runs without them
    assert "minutes-rows" not in process_settings.only_for_format(full, "agenda").checks
