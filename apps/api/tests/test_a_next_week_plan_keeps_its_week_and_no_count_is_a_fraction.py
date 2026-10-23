"""A plan the memo puts in next week keeps its week and gets no day the memo never gives it, and
a count of whole things is not written with a fraction no source states (round-16 T1 report)."""

from __future__ import annotations

import json
import time

import pytest

from app.pipeline import revise_stages
from app.pipeline.report_stages import mark_policy
from app.services import checks, report, stated_days, verify

#: the T1 request with its memo, as sent
MEMO = (
    "아래 이번 주 메모로 팀장님께 올릴 주간 업무 보고서를 만들어줘. 영업2팀 박서연 대리, 2026년 1"
    "0월 3주차(10/12~10/16).\n"
    "\n"
    "[이번 주 메모 - 영업2팀 박서연 대리, 10/12~10/16]\n"
    "- 월: 신규 거래처 '대성유통' 1차 미팅. 월 발주 예상 3,200만원. 단가 협상 잔여(5% 인하 요구).\n"
    "- 화: 기존 거래처 '한솔마트' 재고 클레임 2건 처리 완료(반품 420만원). 원인은 9월 물류센터 이"
    "전 시 출고 지연.\n"
    "- 수: 3분기 마감 자료 정리. 팀 매출 41.2억(목표 40억 대비 103%), 전분기 38.5억 대비 +7%.\n"
    "- 목: 대성유통 견적서 발송. 경쟁사 A사가 동일 품목 4% 낮은 단가 제시한 것으로 파악됨(확정 아"
    "님).\n"
    "- 금: 영업 CRM 데이터 정리 중 중복 거래처 37건 발견, 병합 작업 60% 진행. \n"
    "- 이슈: 영남 출고 지연 재발 우려(물류팀 확인 필요), CRM 병합 완료는 다음주 수요일 예정.\n"
    "- 다음주 계획: 대성유통 단가 최종 협상(목요일 2차 미팅), 한솔마트 재고 점검 방문, CRM 병합 "
    "마무리, 4분기 목표 초안 작성(금요일까"
    "지).\n"
    "- 요청사항: 단가 인하 폭 결재 필요(최대 4%까지 허용 여부), 물류팀 출고 SLA 협의 자리 마련."
)

#: sections 2, 4 and 5 of the T1 report the self-hosted 27B wrote
T1 = [
    {"heading": "주요 업무 추진 결과", "content": (
            "대성유통과의 신규 거래 진행에서 견적 발송은 완료되었으나, 5% 단가 인하 요구가 남아 "
            "있어 최종 단가를 확정하지 못했습니다. 경쟁사 A사의 동"
            "일 품목 4% 낮은 단가 제시 정보는 확인된 사항이 아니라 파악 단계의 내용이며, 이 점을 "
            "기준으로 내부를 검토해야 합니다. 이번 주 대성유통 "
            "관련 세부 처리 현황은 표 2에 정리했습니다.\n"
            "\n"
            "**표 2. 대성유통 신규 거래 진행 내용**\n"
            "\n"
            "| 기준 | 내용 |\n"
            "| --- | --- |\n"
            "| 1차 미팅 및 견적서 발송 | 완료 |\n"
            "| 월 발주 예상 금액 | 3,200만원 |\n"
            "| 고객 단가 인하 요구 | 5% |\n"
            "| 경쟁사 A사 동향(미확정) | 동일 품목 4% 낮은 단가 제시 정보 확인 |\n"
            "\n"
            "클레임 발생 원인은 9월 물류센터 이전 당시 출고 지연으로 기록되어 있으며, 이후 재발 "
            "여부는 물류팀 확인을 거쳐 점검합니다.\n"
            "\n"
            "CRM 데이터 정리 과정에서 발견된 중복 거래처 37건에 대한 병합 작업을 60%까지 진행했습"
            "니다. 병합 완료 목표 일자는 다음 주 수요일로 잡"
            "혀 있으며, 잔여 14.8건(총 37건 × (1 - 60%))을 해당 기간 내 마무리할 예정입니다."
        )},
    {"heading": "다음 주 업무 계획", "content": (
            "대성유통 2차 미팅을 중심으로 협상안을 확정하고, 한솔마트 재고 점검, CRM 병합 마무리,"
            " 4분기 목표 초안 작성 순서로 다음 주를 진행합니다"
            ". 세부 일정은 표 4에 정리했습니다.\n"
            "\n"
            "**표 4. 다음 주 업무 일정**\n"
            "\n"
            "| 일정(요일) | 내용 |\n"
            "| --- | --- |\n"
            "| 화요일 | 한솔마트 재고 점검 방문(클레임 2건 관련 재발 방지 확인) |\n"
            "| 수요일 | CRM 중복 거래처 37건 병합 완료(잔여 14.8건 처리) |\n"
            "| 목요일 | 대성유통 2차 미팅 및 단가 최종 협상 |\n"
            "| 금요일 | 4분기 목표 초안 작성 완료 |\n"
            "\n"
            "경쟁사 A사의 동일 품목 4% 낮은 단가 정보는 미확정 사항으로, 결재를 요청하여 확인을 "
            "마친 후에만 협상 근거로 활용하며 이 점에 대해 상대 "
            "측에 별도 언급하지 않습니다.\n"
            "\n"
            "한솔마트 재고 점검 방문은 물류팀의 출고 SLA 협의 결과와 연계하여 재발 여부를 함께 확"
            "인합니다."
        )},
    {"heading": "결재 및 협조 요청 사항", "content": (
            "```callout\n"
            "결재 대기 중인 단가 협상\n"
            "2026년 10월 3주차 목요일 2차 미팅 전까지 대성유통 단가 인하 폭 결재 결과가 나와야 최"
            "종 단가를 협의할 수 있습니다.\n"
            "```\n"
            "\n"
            "대성유통 단가 인하 폭에 대한 결재를 요청합니다. 고객 측 5% 인하 요구는 허용 한도 4%"
            "를 상회하여, 이번 주 목요일 2차 미팅에서 적용할 "
            "기준을 확정해 주십시오. 월 발주 예상 3,200만원 기준으로 5% 수용 시 160만원(3,200만원"
            " × 5%), 요청 사항인 4% 적용 시 1"
            "28만원(3,200만원 × 4%)의 차감이 발생하며, 표 5에 두 안을 정리했습니다.\n"
            "\n"
            "**표 5. 대성유통 단가 인하 결재 안**\n"
            "\n"
            "| 기준 | 적용률 | 월 발주 차감액 |\n"
            "| --- | --- | --- |\n"
            "| 고객 요구안 | 5% | 160만원 |\n"
            "| 내부 허용 한도안 (확인 필요) | 4% | 128만원 |\n"
            "\n"
            "물류팀과 출고 SLA 협의 자리를 마련해 줄 것을 협조 요청합니다. 영남 권역 출고 지연 재"
            "발 여부와 한솔마트 재고 점검 방문(화요일)이 이 협"
            "의 결과에 따라 진행되므로, 팀장님 또는 물류팀 담당자와 일정 조율이 필요합니다. 협의 "
            "일자와 참석 대상은 확인 후 별도 안내합니다."
        )},
]

VISIT_ROW = "| 화요일 | 한솔마트 재고 점검 방문(클레임 2건 관련 재발 방지 확인) |"
VISIT_SENTENCE = ("영남 권역 출고 지연 재발 여부와 한솔마트 재고 점검 방문(화요일)이 "
                  "이 협의 결과에 따라 진행되므로, 팀장님 또는 물류팀 담당자와 일정 조율이 "
                  "필요합니다.")
CALLOUT = ("2026년 10월 3주차 목요일 2차 미팅 전까지 대성유통 단가 인하 폭 결재 결과가 나와야 최종 "
           "단가를 협의할 수 있습니다.")
THIS_THURSDAY = ("고객 측 5% 인하 요구는 허용 한도 4%를 상회하여, 이번 주 목요일 2차 "
                 "미팅에서 적용할 기준을 확정해 주십시오.")


def _copy(sections: list[dict]) -> list[dict]:
    return json.loads(json.dumps(sections))


def test_the_t1_visit_is_found_on_the_day_the_writer_gave_it_with_words_of_this_weeks_claim():
    found = {piece: items for _, piece, items in stated_days.findings(T1, MEMO)}
    # 「클레임 2건 … 재발」 shares this week's 「- 화:」 line, yet the row says the next-week visit
    assert found[VISIT_ROW] == ["한솔마트 재고 점검 방문(클레임 2건 관련 재발 방지 확인)"]
    assert VISIT_SENTENCE in found
    # next week's 수·목·금 the memo gives stand
    assert not any(row in found for row in (
        "| 수요일 | CRM 중복 거래처 37건 병합 완료(잔여 14.8건 처리) |",
        "| 목요일 | 대성유통 2차 미팅 및 단가 최종 협상 |",
        "| 금요일 | 4분기 목표 초안 작성 완료 |"))
    assert not any("다음 주 수요일" in piece for piece in found)


def test_the_second_meeting_the_memo_puts_next_week_is_found_put_in_this_week():
    found = {piece for _, piece, _ in stated_days.findings(T1, MEMO)}
    # 「이번 주 목요일」 and the report's own 「10월 3주차」 against the memo's
    # 「다음주 계획: … (목요일 2차 미팅)」
    assert THIS_THURSDAY in found and CALLOUT in found
    assert stated_days.untraced("다음 주 목요일 2차 미팅에서 단가를 확정한다.", MEMO) == []
    assert stated_days.untraced("이번 주 목요일 2차 미팅에서 단가를 확정한다.", MEMO)
    # this week's Thursday line stays on this week
    assert not stated_days.untraced("이번 주 목요일에 대성유통 견적서를 발송했다.", MEMO)
    # a next-week plan table puts this week's line in the wrong week
    assert stated_days.untraced("| 목요일 | 대성유통 견적서 발송 |", MEMO, "다음 주 업무 계획")
    assert not stated_days.untraced("| 목요일 | 대성유통 견적서 발송 |", MEMO, "이번 주 성과")
    assert stated_days.week_of("2026년 10월 3주차 목요일", MEMO) == "this"
    assert stated_days.week_of("목요일", MEMO) is None


def test_the_t1_sections_are_marked_where_they_stand_after_the_repair():
    doc = verify.Doc(_copy(T1), MEMO, MEMO, given=MEMO)
    plan, asks = (sec["content"] for sec in mark_policy(doc.sections, [], doc)[1:])
    assert ("| 화요일 | 한솔마트 재고 점검 방문(클레임 2건 관련 재발 방지 확인) (확인 필요) |"
            in plan)
    assert "| 목요일 | 대성유통 2차 미팅 및 단가 최종 협상 |" in plan
    assert "최종 단가를 협의할 수 있습니다 (확인 필요)." in asks
    assert "기준을 확정해 주십시오 (확인 필요)." in asks
    assert "일정 조율이 필요합니다 (확인 필요)." in asks


@pytest.mark.asyncio
async def test_a_repair_that_leaves_the_week_wrong_still_stands_found():
    doc = verify.Doc(_copy(T1[1:2]), MEMO, MEMO, given=MEMO)
    found, _ = await checks.stated_dates(doc)
    row = next(f for f in found if f.sentence == VISIT_ROW)
    assert row.still("| 화요일 | 한솔마트 재고 점검 방문 |")
    assert not row.still("| 미정 | 한솔마트 재고 점검 방문 |")
    # the 다음 주 heading travels with the row: this week's Thursday line is no next-week item
    assert any(f.still("| 목요일 | 대성유통 견적서 발송 |") for f in found)


def test_an_edit_that_moves_the_meeting_into_this_week_is_marked():
    marked = revise_stages._policy_marked("이번 주 목요일 2차 미팅에서 단가를 확정합니다.", MEMO,
                                          [], {})
    assert "단가를 확정합니다 (확인 필요)." in marked


def test_a_count_of_whole_things_with_a_fraction_no_source_states_is_marked():
    second = T1[0]["content"]
    marked = report._without_fractional_counts(second, [MEMO])
    assert "잔여 14.8건(확인 필요)(총 37건 × (1 - 60%))" in marked
    assert report._without_fractional_counts(
        "| 수요일 | CRM 중복 거래처 37건 병합 완료(잔여 14.8건 처리) |", [MEMO]) == (
        "| 수요일 | CRM 중복 거래처 37건 병합 완료(잔여 14.8건(확인 필요) 처리) |")
    # averages and rates, months, and a count the material states stay
    kept = "일평균 2.5건, 1인당 3.5개를 처리했고 3.5개월이 걸렸다. 41.2억 원."
    assert report._without_fractional_counts(kept, [MEMO]) == kept
    assert report._without_fractional_counts("잔여 2.5곳", ["남은 곳 2.5곳"]) == "잔여 2.5곳"
    assert "14.8건(확인 필요)" in revise_stages._figures_marked(
        "잔여 14.8건을 처리한다.", MEMO, [], {}, [])


def test_the_week_and_fraction_checks_finish_on_a_long_run_of_spaces():
    spaces = " " * 50_000
    started = time.perf_counter()
    stated_days.untraced(f"이번 주{spaces}목요일{spaces}", MEMO, spaces)
    stated_days.week_of(f"10월{spaces}3주차{spaces}", MEMO)
    stated_days.findings([{"heading": spaces, "content": f"| 화요일 |{spaces}|"}], MEMO + spaces)
    report._without_fractional_counts(f"1.5{spaces}건{spaces}당", [spaces])
    assert time.perf_counter() - started < 0.5
