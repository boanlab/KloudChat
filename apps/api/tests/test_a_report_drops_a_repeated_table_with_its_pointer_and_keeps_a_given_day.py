"""A repeated table goes with the sentence pointing at it (「〔표〕는 …정리한 것입니다」), and a
plan row on the day the memo's next-week line gives (「- 다음 주: 화요일 결제 오류 사후 검토
회의」) stands though it adds words from this week's 화 line. Shape from the round 16 audit (H3)."""

from __future__ import annotations

import time

from app.services import report, stated_days

FIRST = ("| 항목 | 값 |\n| --- | --- |\n| 주간 상담 합계 | 6,020건 |\n| 주간 목표 | 6,500건 |\n"
         "| 이월 미해결 | 87건 |")
AGAIN = ("| 항목 | 값 | 산출 근거 |\n| --- | --- | --- |\n"
         "| 주간 상담 합계 | 6,020건 | 주간 실적 |\n"
         "| 주간 목표 | 6,500건 | 주간 목표 |\n| 이월 미해결 | 87건 | 다음 주 이월 |")


def test_a_repeated_table_goes_with_the_sentence_pointing_at_it():
    sections = [
        {"heading": "주간 실적", "content": f"주간 합계는 6,020건입니다.\n\n{FIRST}"},
        {"heading": "결과", "content": (
            "미달분은 480건입니다. 〔표〕는 이번 주 지표를 값과 산출 근거로 정리한 것입니다.\n\n"
            f"표: 주간 상담 운영 결과 값\n\n{AGAIN}\n\n다음 주에는 이월분을 처리합니다.")},
    ]
    out = report.drop_repeated_tables(sections)
    body = out[1]["content"]
    assert "산출 근거" not in body and "정리한 것입니다" not in body
    assert body.startswith("미달분은 480건입니다.") and "이월분을 처리합니다" in body
    # a pointer line alone goes whole
    alone = [sections[0], {"heading": "결과", "content": f"아래 표는 지표입니다.\n\n{AGAIN}"}]
    assert report.drop_repeated_tables(alone)[1]["content"].strip() == ""


MEMO = ("[이번 주 메모 - 고객지원팀, 10/19~10/23]\n"
        "- 화: 환불 문의 212건으로 급증. 원인은 10/16 결제 모듈 오류. "
        "개발팀에 재발 방지 대책 요청함.\n"
        "- 목: 신규 상담원 3명 교육 시작(2주 과정).\n"
        "- 다음 주: 화요일 결제 오류 사후 검토 회의, 목요일 상담 스크립트 개정안 공유.")


def test_a_row_on_the_day_the_memo_gives_stands():
    row = "| 화요일 | 결제 오류 사후 검토 회의(재발 방지 대책·처리 일정 확인) |"
    assert stated_days.untraced(row, MEMO, "**표 3. 다음 주 일정**") == []
    # this week's 화 line does not put the next-week meeting on this week's 화요일
    assert stated_days.untraced("| 화요일 | 결제 오류 사후 검토 회의 |", MEMO, "이번 주 실적") != []


def test_fifty_thousand_spaces_finish_quickly():
    gap = " " * 50_000
    started = time.perf_counter()
    report.drop_repeated_tables([
        {"heading": "a", "content": FIRST},
        {"heading": "b", "content": f"아래{gap}표는{gap}.\n\n{AGAIN}"}])
    assert time.perf_counter() - started < 0.5
