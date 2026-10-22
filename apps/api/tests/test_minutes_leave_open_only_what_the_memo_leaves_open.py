"""Minutes leave open only what the memo leaves open: no question about an owner the memo
never asked for, no claim that every deadline is set beside a 미정 row, no hedge, promise or
part of the day the memo never gives, and no 「제목:/본문:」 lines however the skill's own text
speaks of mails and next meetings."""

from __future__ import annotations

import time

from test_minutes_say_only_what_the_memo_says import SALES, TUTOR

from app.services import report_checks, stated_meeting
from app.services.verify import Doc

#: a minutes agent's skill text, given beside the memo
SKILL = ("# 활성 스킬 — 회의록 작성\nAdd 출처(회의/메일 n) only when follow-up mails are part "
         "of the material. Then 다음 회의 (일시·장소·예정 안건) when the material gives it.")

TUTOR_DECIDED = ("**표 1. 결정 사항**\n\n| 항목 | 내용 | 비고 |\n|---|---|---|\n"
                 "| 간식비 예산 | 80만원 요청을 50만원으로 조정하여 합의 | 팀장 제안 |")
TUTOR_ACTIONS = ("**표 2. 실행 과제**\n\n| 항목 | 담당 | 기한 | 비고 |\n|---|---|---|---|\n"
                 "| 2학기 튜터링 포스터 시안 준비 | 이수연 | 2026-10-23(금) | 다음 주 금요일 |")
INVENTED = ("확인 필요: 80만원에서 50만원으로 조정된 간식비 예산의 실제 지출 집행 담당자가 "
            "회의에서 지정되지 않았습니다.")
SALES_ACTIONS = ("| 번호 | 할 일 | 담당 | 기한 | 비고 |\n|---|---|---|---|---|\n"
                 "| 1 | 후보 리스트 10곳 정리 | 한지민 | 2026-10-23(금) | |\n"
                 "| 2 | 새 견적 양식 초안 공유 | 류태호 | 2026-10-28(수) | |\n"
                 "| 3 | 출장비 영수증 누락 3건 제출 | 서은우 | 미정 | 이번 주 안에 |")


def test_an_owner_the_memo_never_leaves_open_is_no_open_point():
    sections = [{"heading": "결정 사항", "content": TUTOR_DECIDED},
                {"heading": "실행 과제", "content": f"{TUTOR_ACTIONS}\n\n{INVENTED}"}]
    found = stated_meeting.findings(sections, TUTOR)
    assert [p for _, p, _ in found] == [INVENTED]
    weak = report_checks.settle_unsourced(sections, TUTOR, strong=False)
    assert "집행 담당자" not in weak[1]["content"]
    assert weak[1]["content"].endswith("| 다음 주 금요일 |")
    # a strong writer's is marked where it stands
    strong = report_checks.settle_unsourced(sections, TUTOR, strong=True)
    assert "지정되지 않았습니다 (확인 필요)." in strong[1]["content"]


def test_what_the_memo_or_a_table_row_leaves_open_stays_open():
    rows = stated_meeting.open_rows([{"heading": "", "content": SALES_ACTIONS}])
    for held in ("※ 3번 기한은 회의에서 확정된 날짜로 정하지 않아 확인이 필요합니다.",
                 "출장비 영수증 누락 건의 제출 기한은 「이번 주 안」으로만 언급되어 확정 날짜가 "
                 "없습니다."):
        assert not stated_meeting.untraced(held, SALES, rows=rows)
    # the memo's own 「결정 못 함」
    assert not stated_meeting.untraced("구글폼과 네이버폼 중 무엇을 쓸지는 결정하지 못했습니다.",
                                       TUTOR, rows=[])
    # a part of the head the memo lacks is a field, not a question
    assert not stated_meeting.untraced("회의 장소는 자료에 명시되지 않았습니다.", TUTOR, rows=[])
    # no table: nothing to judge by
    assert not stated_meeting.untraced(INVENTED, TUTOR)


def test_every_deadline_called_set_beside_a_blank_row_is_found():
    claim = "세 안건 모두 책임자와 일정이 명시적으로 지정되었습니다."
    blank = [{"heading": "결정 사항", "content": f"{SALES_ACTIONS}\n\n{claim}"}]
    assert [p for _, p, _ in stated_meeting.findings(blank, SALES)] == [claim]
    assert report_checks.settle_unsourced(blank, SALES, strong=False)[0]["content"] \
        == SALES_ACTIONS
    filled = [{"heading": "결정 사항", "content": SALES_ACTIONS.replace(
        "| 미정 | 이번 주 안에 |", "| 2026-10-23(금) | 이번 주 안에 |") + f"\n\n{claim}"}]
    assert not stated_meeting.findings(filled, SALES)
    # 「모두 정하지 않았다」 calls nothing set
    assert not stated_meeting.untraced("세 안건 모두 기한을 정하지 않았습니다.", SALES,
                                       rows=stated_meeting.open_rows(blank))


def test_a_hedge_a_promise_and_a_part_of_the_day_the_memo_never_gives_are_found():
    hedge = ("예정 안건으로는 최다은이 준비하는 구글폼·네이버폼 비교표 검토를 포함할 수 있으나, "
             "추가 안건은 확정되는 대로 안내합니다.")
    assert stated_meeting.untraced(hedge, TUTOR, next_part=True)
    assert stated_meeting.untraced("일정은 추후 공지할 예정입니다.", TUTOR)
    assert stated_meeting.untraced("적용 시작 시점은 승인 완료 여부에 따라 달라질 수 있습니다.",
                                   SALES)
    assert stated_meeting.untraced("회의는 오전 시간대에 진행되었습니다.", TUTOR) == ["오전"]
    # the memo's own: its agenda, its 10시, its 오후, what someone may do
    for own in ("예정 안건은 최다은의 구글폼·네이버폼 비교표 검토입니다.",
                "다음 회의는 10월 21일 오전 10시에 3층 회의실에서 열립니다."):
        assert not stated_meeting.untraced(own, TUTOR, next_part=True)
    assert not stated_meeting.untraced("회의는 오후 2시에 열렸습니다.", SALES)
    assert not stated_meeting.untraced("새 양식은 팀장 승인 후 사용할 수 있습니다.", SALES)
    # the hedge goes once its names stand elsewhere
    sections = [{"heading": "실행 과제",
                 "content": "최다은이 구글폼·네이버폼 비교표를 준비합니다."},
                {"heading": "다음 회의", "content": f"다음 회의는 10월 21일에 열립니다. {hedge}"}]
    weak = report_checks.settle_unsourced(sections, TUTOR, strong=False)
    assert weak[1]["content"] == "다음 회의는 10월 21일에 열립니다."


def test_the_skills_words_on_mails_and_next_meetings_are_no_memo():
    sections = [
        {"heading": "회의 개요", "content": (
            "영업1팀 주간회의가 열렸습니다.\n\n`\n\n제목: 영업1팀 주간회의 일정\n\n"
            "본문: 이번 주 처리가 필요한 항목을 확정하기 위해 회의를 개최합니다.\n\n`")},
        {"heading": "다음 회의", "content": (
            "다음 회의는 10월 28일(수) 오후 2시에 열립니다. 회의 예정 안건은 회의 자료에 "
            "제시되지 않았습니다.")},
    ]
    given = f"{SALES}\n{SKILL}"
    # read against everything given, the skill's 「메일」 hides the mail lines
    hidden = report_checks.minutes_unsourced(Doc(sections, material=given, given=given))
    assert not any(f.sentence.startswith(("제목", "본문")) for f in hidden)
    found = report_checks.minutes_unsourced(Doc(sections, material=given, given=given, memo=SALES))
    assert len(found) == 3
    out = report_checks.settle_unsourced(sections, SALES, strong=True)
    assert out[0]["content"] == "영업1팀 주간회의가 열렸습니다."


def test_the_open_point_checks_finish_on_a_long_run_of_spaces():
    spaces = " " * 50_000
    started = time.perf_counter()
    for text in (f"모두{spaces}담당{spaces}지정", f"확정{spaces}대로", f"지정{spaces}않",
                 f"`{spaces}\n{spaces}\n`", f"-{spaces}제목:", f"오전{spaces}에",
                 f"달라질{spaces}수{spaces}있"):
        stated_meeting.untraced(text, SALES, rows=[("1", {"출장비"})])
        stated_meeting.settle(text, text, [text], strong=False, material=SALES)
    assert time.perf_counter() - started < 0.5
