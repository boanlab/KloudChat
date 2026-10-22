"""Checks narrowed to what the material leaves out: an English memo's approval is no Korean
rule the check can trace, a day the memo item gives stands though its words sit in another clause
or the heading names another week, no request sentence or 「11월 말」 is read as a meeting's name,
a sentence keeping a thing said nowhere else stays, a decision told with an agreement verb keeps
its facts, and an action row whose task a memo person was given stays. Shapes from the round 16
audit (H1, H2, H3, T1, tc, tc2 and the clean answers)."""

from __future__ import annotations

import time

from app.services import meeting_memo, report_checks, stated_days, stated_policy

T1 = ("- 금: 영업 CRM 데이터 정리 중 중복 거래처 37건 발견, 병합 작업 60% 진행.\n"
      "- 이슈: CRM 병합 완료는 다음주 수요일 예정.\n"
      "- 다음주 계획: 대성유통 단가 최종 협상(목요일 2차 미팅), 한솔마트 재고 점검 방문.\n"
      "- 목: 대성유통 견적서 발송.\n- 화: 한솔마트 재고 클레임 2건 처리 완료.")
H3 = ("- 화: 환불 문의 212건으로 급증. 개발팀에 재발 방지 대책 요청함.\n"
      "- 다음 주: 화요일 결제 오류 사후 검토 회의, 목요일 상담 스크립트 개정안 공유.")
H1 = ("10/20 물류운영팀 주간회의. 참석 한도윤(팀장), 서지민, 오태경, 문가람. 안건1 연말 성수기 "
      "임시 인력: 8명으로 줄이고 모자라는 몫은 기존 인원 야간 연장근무로 메우기로 바꿈. 서지민이 "
      "8명 기준 재견적을 10/23(금)까지 받아 오기로 함.")


def _sec(heading: str, content: str) -> dict:
    return {"heading": heading, "content": content}


def test_an_english_memo_s_approval_is_no_untraced_korean_one():
    memo = "Oct 19 Product Design weekly sync. 2) Icon set: the outlined style was approved."
    assert stated_policy.untraced("아이콘 세트 스타일이 최종적으로 승인되었습니다.", memo) == []
    # a Korean memo that only asks still leaves the approval untraced
    assert stated_policy.untraced("단가 인하 폭은 승인되었습니다.", "인하 폭 결재 필요") != []


def test_a_day_the_memo_item_gives_stands():
    told = "CRM 중복 거래처 37건은 병합 작업이 60% 진행되었고, 완료 시점은 다음 주 수요일입니다."
    assert stated_days.untraced(told, T1) == []
    review = "화요일 사후 검토 회의에서는 개발팀 재발 방지 대책 요청 건의 결과를 확인합니다."
    assert stated_days.untraced(review, H3, "다음 주 계획") == []
    # a week the sentence names itself still has to be that week's
    moved = "이번 주 목요일 대성유통 2차 미팅에서 단가를 최종 협상합니다."
    assert stated_days.untraced(moved, T1) != []


def test_no_request_sentence_or_month_part_is_read_as_the_meeting():
    asked = meeting_memo.read("첨부한 녹취 텍스트로 회의록을 만들어줘. 참석: 김PM")
    assert asked.this.name == ""
    short = meeting_memo.read("(짧은 메모) 어제 협력사 미팅: 납기 11월 말로 합의됐다고 들음.")
    assert short.this.name == "어제 협력사 미팅"
    named = meeting_memo.read("10/14 교육혁신팀 주간회의. 참석 김지훈")
    assert named.this.name == "교육혁신팀 주간회의"


def test_a_copied_line_holding_a_thing_said_nowhere_else_stays():
    memo = "간편결제는 PG 계약(이번 달 말 목표) 후 재논의."
    sections = [_sec("논의", "간편결제는 PG 계약(이번 달 말 목표) 후 재논의."),
                _sec("결정", "PG 계약을 이번 달 말까지 맺기로 했습니다.")]
    out, _ = meeting_memo.settle_copies(sections, memo, strong=False)
    assert out == sections


def test_a_decision_told_with_an_agreement_verb_keeps_its_facts():
    told = ("연말 성수기 임시 인력은 단기 아르바이트 8명으로 정하고, 부족한 물량은 기존 인원 야간 "
            "연장근무로 충당하기로 합의했습니다.")
    out = report_checks.settle_unsourced([_sec("결정 사항", told)], H1, strong=False)
    assert out[0]["content"] == told.replace("합의했습니다", "정했습니다")
    header = "| 항목 | 합의 내용 |\n| --- | --- |\n| 인력 | 8명 |"
    assert report_checks.settle_unsourced([_sec("결정 사항", header)], H1,
                                          strong=False)[0]["content"] == header


def test_a_row_whose_task_a_memo_person_was_given_stays():
    table = ("| 할 일 | 담당 | 기한 |\n| --- | --- | --- |\n"
             "| 임시 인력 8명 기준 재견적 수령 | 미정 | 2026-10-23(금) |\n"
             "| 연말 회식 장소 예약 | 미정 | 미정 |")
    rows = report_checks.unassigned_rows([_sec("실행 항목", table)], H1)
    assert [task for _, _, task in rows] == [["연말 회식 장소 예약"]]
    decided = "| 결정 | 담당 | 기한 |\n| --- | --- | --- |\n| 회식 장소 확정 | 미정 | 미정 |"
    assert report_checks.unassigned_rows([_sec("결정 사항", decided)], H1) == []


def test_the_narrowed_checks_finish_on_a_long_run_of_spaces():
    gap = " " * 50_000
    started = time.perf_counter()
    meeting_memo.read(f"({gap}) 협력사{gap}미팅: 납기")
    stated_days.untraced(f"병합은{gap}진행되었고, 완료는{gap}수요일입니다.", T1)
    meeting_memo.settle_copies([_sec("a", f"간편결제는{gap}재논의.")], "간편결제 재논의",
                               strong=False)
    assert time.perf_counter() - started < 0.5
