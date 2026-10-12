"""Default 회의록 path (verify2 N7, N8, Q16): stacked action tables stay two tables, a
combined 「담당·기한」 column is not blanked, 「미정(사유)」 keeps the reason out of the owner
cell, 「다음 회의 때」 is the next meeting's date the memo gives, and today's date is no
deadline."""

import datetime as dt

from app.services import report, report_checks, richtext, skill_scripts

MEMO = """아래 메모로 회의록 작성해 줘. 결정 사항과 할 일(담당·기한)이 한눈에 보이게.

[메모] 10/8(목) 14시 본관 3층 소회의실, 교육혁신팀 주간회의
참석: 박정민 팀장, 이수진 차장, 김도윤 대리, 최하늘 주임 (정현우 과장 출장으로 불참)
2. LMS 개편 — 업체(한결소프트) 1차 시안 10/15 도착 예정. 김도윤: 모바일 화면 검수 \
체크리스트 만들어서 다음 회의 때 공유하겠다.
3. 우수 강의 사례집 — 미제출 3편은 교수님들께 10/16까지 재요청(최하늘). 결론 못 냄 → \
정현우 과장 복귀 후 다음 회의에서 결정.
4. 기타: 재무팀에서 10/30까지 11월 예산 집행계획 제출 요청 옴. 팀장이 각자 담당 사업 \
집행계획을 10/23까지 본인에게 달라고 함.
다음 회의 10/15(목) 14시 같은 장소"""

STACKED = """| 항목 | 담당 | 기한 | 비고 |
|---|---|---|---|
| 모바일 화면 검수 체크리스트 작성 및 공유 | 김도윤 | 미정 | 다음 회의 때 공유 |
| 미제출 원고 3편 재요청 | 최하늘 | 2026-10-16(금) | 교수님들께 10/16까지 재요청 |
| 항목 | 담당 | 기한 | 비고 |
|---|---|---|---|
| 디자인 업체 최종 결정 | 미정(정현우 과장 복귀 후) | 미정(다음 회의에서) | - |"""

COMBINED = """| 안건 | 논의·결정 내용 | 후속 작업(담당·기한) |
| --- | --- | --- |
| 11월 예산 집행계획 | 재무팀 10/30까지 제출 요청 | \
담당 사업별 집행계획 제출(박정민 팀장에게, 10/23까지) / 재무팀 제출([담당자], 10/30까지) |"""


def test_a_second_header_keeps_its_table_apart():
    split = richtext.tidy_tables(report.split_glued_tables(STACKED))
    assert "공유 |\n| 미제출" in split and "재요청 |\n\n| 항목 | 담당" in split
    found = report_checks.tables([{"heading": "후속 조치", "content": STACKED}])
    assert [len(t.rows) for t in found] == [2, 1]


def test_minutes_repair_adds_columns_without_turning_a_rule_into_a_row():
    sections = [{"heading": "후속 조치", "content": STACKED}]
    widened, _ = report_checks.minutes_columns(sections, [])
    grounded, _ = report_checks.minutes_grounding(widened, MEMO)
    body = grounded[0]["content"]
    assert "[담당자] | --- |" not in body and "| --- |" not in body.replace("|---|", "")
    assert body.count("| 항목 | 담당 | 기한 | 비고 | 출처 |") == 2


def test_a_combined_owner_and_due_column_is_left_whole():
    sections = [{"heading": "결정 사항", "content": COMBINED}]
    widened, _ = report_checks.minutes_columns(sections, [])
    grounded, codes = report_checks.minutes_grounding(widened, MEMO)
    assert "담당 사업별 집행계획 제출(박정민 팀장에게, 10/23까지)" in grounded[0]["content"]
    assert "minutes_owner" not in codes


def test_a_deferred_cell_keeps_its_reason_in_the_note_not_in_the_owner():
    out, moved = report_checks.minutes_deferred([{"heading": "후속 조치", "content": STACKED}])
    assert moved == 1
    row = "| 디자인 업체 최종 결정 | 미정 | 미정 | 정현우 과장 복귀 후, 다음 회의에서 |"
    assert row in out[0]["content"]


def test_next_meeting_is_the_date_the_memo_gives():
    rows = skill_scripts.dates().scan(MEMO, dt.date(2026, 10, 8))
    said = [r for r in rows if r["text"].startswith("다음 회의 때")]
    assert said and said[0]["status"] == "ok" and said[0]["date"] == "2026-10-15"
    table = [{"heading": "후속 조치", "content": STACKED.split("\n| 항목")[0]}]
    filled, count = report_checks.minutes_due_fill(table, rows)
    assert count == 1
    row = "| 모바일 화면 검수 체크리스트 작성 및 공유 | 김도윤 | 2026-10-15(목) |"
    assert row in filled[0]["content"]


def test_today_is_no_deadline_in_minutes():
    leak = "또한, 10월 11일(일)과 10월 23일(금)의 마감 기한이 있는 예산 업무를 이행해야 합니다."
    out = report.blank_today(leak, MEMO, dt.date(2026, 10, 11))
    assert out == "또한, [일자]와 10월 23일(금)의 마감 기한이 있는 예산 업무를 이행해야 합니다."
    # A date the memo gives stays, even on the day it is written.
    assert report.blank_today("10월 15일(목) 다음 회의", MEMO, dt.date(2026, 10, 15)) == (
        "10월 15일(목) 다음 회의")
