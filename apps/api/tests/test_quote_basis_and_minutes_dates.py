"""The quote names its VAT basis, and minutes bind dates to their own rows and never invent
a meeting date (lineup v1 client-proposal-3, minutes-2, minutes-3)."""

from __future__ import annotations

import datetime as dt
import json

from app.services import report, report_checks, skill_scripts
from app.services.verify import Doc, find
from tests.test_a_quote_leaves_out_what_the_rfp_rules_out import KAFI_REPLY, KAFI_REQUEST, KAFI_RFP
from tests.test_minutes_keep_to_the_material import MEMO_3, REQUEST_3, SPOKEN

# ── client-proposal-3: 85,000,000 is the 공급가액 ──────────────────────────

#: The extraction took the budget's 「부가가치세 포함」 for the memo's prices.
MISREAD = {**KAFI_REPLY, "vat_basis": "incl"}
#: The run's 견적 section, as written from a block that called 85,000,000 「총액」.
RUN_QUOTE = (
    "제안 총액은 부가세 포함 기준입니다.\n\n**표 3. 견적(부가세 포함 기준)**\n\n"
    "| 항목 | 수량 | 단위 | 단가 | 금액 | 비고 |\n|---|---|---|---|---|---|\n"
    "| PM | 1명 × 5개월 | 명·월 | 7,000,000원 | 35,000,000원 |  |\n"
    "| 퍼블리셔 | 2명 × 4개월 | 명·월 | 5,500,000원 | 44,000,000원 |  |\n"
    "| 접근성 진단 전문가 | 1명 × 1개월 | 명·월 | 6,000,000원 | 6,000,000원 |  |\n"
    "| 공급가액 |  |  |  | 77,272,727원 | 위 3개 항목 합계 |\n"
    "| 부가세(10%) |  |  |  | 7,727,273원 |  |\n"
    "| 총액 |  |  |  | 85,000,000원 | 부가세 포함 |\n\n"
    "85,000,000원 × 0.10 = 8,500,000원(부가세)이 견적 확정 블록의 7,727,273원과 다릅니다.\n\n"
    "소요예산 99,000,000원(부가세 포함)과 견적 총액 85,000,000원(부가세 포함)은 같은 기준이므로 "
    "여유가 있습니다."
)


def _material() -> str:
    return report._request_material(KAFI_REQUEST) + "\n\n" + KAFI_RFP


def test_prices_with_no_stated_basis_are_vat_exclusive_whatever_the_extraction_says():
    data, _ = report_checks.grounded_quote_input(MISREAD, _material())
    assert data["vat_basis"] == "excl" and data["basis_assumed"]
    result = skill_scripts.quotes().run(data)
    assert (result["supply"], result["vat"], result["total"]) == (85_000_000, 8_500_000, 93_500_000)


async def test_the_block_labels_every_amount_with_its_basis():
    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(MISREAD, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    pre = await report_checks.prepare("client_proposal", KAFI_REQUEST, _material(),
                                      complete=complete, model="m", api_key="k")
    block = next(b for b in pre.blocks if b.startswith("# 견적 확정 블록"))
    assert "| 공급가액(VAT 별도) |  |  |  | 85,000,000 |" in block
    assert "| 합계(VAT 포함) |  |  |  | 93,500,000 |" in block
    assert "- 공급가액(VAT 별도): 금85,000,000원" in block
    assert "- 부가세: 8,500,000원" in block and "- 합계(VAT 포함): 금93,500,000원" in block
    assert "| 총액 |" not in block and "- 총액" not in block
    run = next(r for r in pre.runs if r["skill"] == "quote-calc")
    assert "공급가액(VAT 별도) 85,000,000원, 합계(VAT 포함) 93,500,000원" in run["summary"]
    figures = {f.name: f.value for f in pre.figures}
    assert figures["견적 합계(VAT 포함)"] == "93,500,000원"


async def test_a_back_computed_quote_and_a_wrong_total_are_found():
    data, _ = report_checks.grounded_quote_input(MISREAD, _material())
    quote = skill_scripts.quotes().run(data)
    doc = Doc([{"heading": "견적", "content": RUN_QUOTE}], _material())
    found, _ = await find(doc, report_checks.checks(report_checks.Prepass(
        format_id="client_proposal", quote=quote)))
    flagged = {f.sentence for f in found if f.code == "quote_total"}
    assert any("77,272,727" in s for s in flagged)  # the 공급가액 row
    assert any(s.startswith("| 총액 |") for s in flagged)
    assert any("견적 총액 85,000,000원(부가세 포함)" in s for s in flagged)
    prose = next(f for f in found if "견적 총액 85,000,000원" in f.sentence)
    assert "93,500,000원" in prose.why
    fixed = prose.sentence.replace("85,000,000원(부가세 포함)", "93,500,000원(부가세 포함)")
    assert not prose.still(fixed)


async def test_a_right_quote_is_left_alone():
    data, _ = report_checks.grounded_quote_input(KAFI_REPLY, _material())
    quote = skill_scripts.quotes().run(data)
    right = (
        "| 항목 | 금액 |\n|---|---:|\n| 공급가액(VAT 별도) | 85,000,000원 |\n"
        "| 부가세 | 8,500,000원 |\n"
        "| 합계(VAT 포함) | 93,500,000원 |\n\n견적 합계(VAT 포함)는 93,500,000원이고 공급가액은 "
        "85,000,000원(VAT 별도)이다. PM 합계 35,000,000원. 예산 대비 여유는 5,500,000원이다."
    )
    found, _ = await find(Doc([{"heading": "견적", "content": right}], _material()),
                          report_checks.checks(report_checks.Prepass(
                              format_id="client_proposal", quote=quote)))
    assert [f for f in found if f.code == "quote_total"] == []


def test_the_writer_s_arithmetic_about_the_block_never_reaches_the_document():
    body = report.drop_prompt_talk(RUN_QUOTE)
    assert "확정 블록" not in body and "× 0.10" not in body
    assert "소요예산 99,000,000원" in body and "| 총액 |" in body
    assert report.drop_prompt_talk(
        "공급가액 1,000,000원 × 0.1 = 100,000원이 위 값과 다르다. 다음 문장.") == "다음 문장."


# ── minutes-2: one recurring line, one row ─────────────────────────────

RECORDING = "## 녹음_연구실주간미팅_20261014_1000.mp3\n" + SPOKEN
#: The run's table before the fill: three rows left 미정, the recurring one written.
RUN_ROWS = (
    "| 할 일 | 담당 | 기한 | 출처 | 비고 |\n| :--- | :--- | :--- | :--- | :--- |\n"
    "| 데이터 정제 수행 | 서○○ | 2026-10-21(수) | 회의 | 원문 표현: '다음 주 수요일까지' |\n"
    "| GPU 서버 우선 사용 보장 | 오○○ | 미정 | 회의 | 원문 표현: '다음 주까지는' |\n"
    "| GPU 서버 예약표 갱신 | 문○○ | 매주 월요일(2026-10-19부터) | 회의 "
    "| 원문 표현: '다음 주부터 매주 월요일에' |\n"
    "| 부산 학회 연구비 확인 | 한교수 | 미정 | 회의 | 확인 전까지 숙소 예약 불가 |\n"
    "| 학회 숙소 예약 검토 | 서○○, 문○○ | 미정 | 회의 | 연구비 확인 완료 후 수행 |\n"
)


async def _minutes(material: str, request: str = "회의록으로 정리해 줘.",
                   today: dt.date | None = dt.date(2026, 10, 8)) -> report_checks.Prepass:
    async def no_model(*args, **kwargs):
        raise AssertionError("minutes take no model call here")

    return await report_checks.prepare("minutes", request, material, complete=no_model,
                                       model="m", api_key="k", today=today)


async def test_a_recurring_deadline_goes_only_to_the_row_that_names_its_task():
    pre = await _minutes(RECORDING)
    filled, _ = report_checks.minutes_due_fill([{"heading": "실행 항목", "content": RUN_ROWS}],
                                               pre.dates)
    content = filled[0]["content"]
    assert content.count("매주 월요일") == 1 + content.count("원문 표현: '다음 주부터 매주")
    assert "| 부산 학회 연구비 확인 | 한교수 | 미정 |" in content
    assert "| 학회 숙소 예약 검토 | 서○○, 문○○ | 미정 |" in content
    assert "| GPU 서버 우선 사용 보장 | 오○○ | 매주" not in content


async def test_a_recurring_deadline_on_another_row_is_found():
    pre = await _minutes(RECORDING)
    wrong = RUN_ROWS.replace("| 부산 학회 연구비 확인 | 한교수 | 미정 |",
                             "| 부산 학회 연구비 확인 | 한교수 | 매주 월요일(2026-10-19부터) |")
    found, _ = await report_checks.minutes_rows(
        Doc([{"heading": "실행 항목", "content": wrong}]), pre.dates)
    rows = [f.sentence for f in found if "반복 일정" in f.why]
    assert len(rows) == 1 and "연구비 확인" in rows[0]


# ── minutes-3: no meeting date anywhere ──────────────────────────────

#: Excerpts of minutes-3 r1 as written, from a base of the day the memo came in.
RUN_OVERVIEW = (
    "2026년 10월 8일에 열린 축제 부스 준비 회의에서는 부스 위치와 운영 메뉴를 확인했습니다."
)
RUN_ACTIONS = (
    "| 항목 | 담당 | 기한 | 출처 | 비고 |\n| --- | --- | --- | --- | --- |\n"
    "| 홍보 포스터 제작 | 수아 | 다음 주 금요일 | 회의 | 지민에서 변경 |\n"
    "| 재료 업체 견적 요청 | 도윤 | 2026-10-10(토) | 회의 | - |\n"
    "| 위생교육 이수증 제출 | [담당자] | [기한] | 회의 | 학생처 제출 필요 |\n"
)


def test_no_meeting_date_unless_the_text_the_file_or_the_person_gives_one():
    assert report_checks.meeting_base(REQUEST_3, MEMO_3, dt.date(2026, 10, 8)) == (None, "")
    today = dt.date(2026, 10, 8)
    assert report_checks.meeting_base("오늘 회의 메모야. 회의록 써 줘.", MEMO_3, today) == (
        today, "said:오늘")
    assert report_checks.meeting_base("어제 한 동아리 회의 정리해 줘.", MEMO_3, today) == (
        dt.date(2026, 10, 7), "said:어제")
    # A due day or the document is not the meeting's day.
    assert report_checks.meeting_base("오늘까지 회의록 만들어 줘.", MEMO_3, today) == (None, "")
    assert report_checks.meeting_base("오늘 회의록 만들어 줘.", MEMO_3, today) == (None, "")


async def test_relative_deadlines_stay_words_and_the_step_says_why():
    pre = await _minutes(MEMO_3, REQUEST_3)
    assert pre.undated and pre.base_date == ""
    assert {r["text"] for r in pre.dates if r["status"] == "no_base"} >= {"다음 주 금요일", "모레"}
    assert not pre.bound_dates
    run = next(r for r in pre.runs if r["skill"] == "date-calc")
    assert "2026-10-08" not in run["summary"] and "자료를 받은 날" not in run["summary"]
    assert "[회의 일자]" in run["summary"] and "기한 [기한], 원문은 비고" in run["summary"]
    block = next(b for b in pre.blocks if b.startswith("# 확정 날짜"))
    assert "「[회의 일자]」" in block and "2026-10" not in block
    assert "「다음 주 금요일」 → not resolved (no meeting date): 기한 「[기한]」" in block


async def test_said_today_reads_from_the_turn_s_date_and_says_so():
    pre = await _minutes(MEMO_3, "오늘 축제 준비 회의 때 적은 거야. 회의록으로 만들어 줘.")
    assert pre.base_date == "2026-10-08" and not pre.undated
    run = next(r for r in pre.runs if r["skill"] == "date-calc")
    assert "요청의 「오늘」을 대화 날짜(한국 시간)로 셈" in run["summary"]


async def test_an_invented_meeting_date_and_worked_out_deadlines_become_blanks():
    pre = await _minutes(MEMO_3, REQUEST_3)
    sections = [{"heading": "회의 개요", "content": RUN_OVERVIEW},
                {"heading": "실행 항목", "content": RUN_ACTIONS}]
    fixed, codes = report_checks.minutes_undated(sections, pre.dates, f"{REQUEST_3}\n{MEMO_3}")
    assert fixed[0]["content"].startswith("[회의 일자]에 열린 축제 부스 준비 회의에서는")
    table = fixed[1]["content"]
    assert ("| 홍보 포스터 제작 | 수아 | [기한] | 회의 | 지민에서 변경, 원문 「다음 주 금요일」, "
            "회의 일자 미기재로 날짜 미확정 |") in table
    assert ("| 재료 업체 견적 요청 | 도윤 | [기한] | 회의 | 원문 「모레」, 회의 일자 미기재로 "
            "날짜 미확정 |") in table
    assert "| 위생교육 이수증 제출 | [담당자] | [기한] | 회의 | 학생처 제출 필요 |" in table
    assert codes.count("minutes_meeting_day") == 1 and codes.count("minutes_undated_due") == 2
    # A day the material itself writes stays.
    kept, _ = report_checks.minutes_undated(
        [{"heading": "개요", "content": "10월 21일에 열린 회의에서 정했다."}], pre.dates,
        "10월 21일 회의 메모")
    assert kept[0]["content"] == "10월 21일에 열린 회의에서 정했다."


async def test_the_head_s_date_is_the_read_one_or_a_blank():
    head = {"format": "minutes", "fields": [["회의명", "축제 부스 회의"], ["일시", ""]]}
    pre = await _minutes(MEMO_3, REQUEST_3)
    assert report_checks.minutes_head(head, pre)["fields"][1] == ["일시", "[회의 일자]"]
    filed = await _minutes(RECORDING)
    assert report_checks.minutes_head(head, filed)["fields"][1] == ["일시", "2026-10-14(수)"]
    given = {**head, "fields": [["회의명", "x"], ["일시", "2026-10-21 19:00"]]}
    assert report_checks.minutes_head(given, pre) is None


def test_a_memo_day_with_its_weekday_is_the_meeting_date():
    # Quality lane Q16: 「[메모] 10/8(목) 14시 … 주간회의」 left 「회의 일자: [회의 일자]」.
    import datetime as _dt

    from app.services import report_checks as _rc

    memo = "[메모] 10/8(목) 14시 본관 3층 소회의실, 교육혁신팀 주간회의"
    today = _dt.date(2026, 10, 11)
    assert _rc.meeting_date("아래 메모로 회의록 작성해 줘.", memo, today) == _dt.date(2026, 10, 8)
    assert _rc.meeting_base("회의록 작성해 줘.", memo, today) == (_dt.date(2026, 10, 8), "text")
    # A weekday no year near today has (10/8 is a Thursday in 2026 only): still asked.
    assert _rc.meeting_date("", "10/8(월) 주간회의", today) is None
    # Without a weekday the year is not guessed.
    assert _rc.meeting_date("", "10/8 14시 주간회의", today) is None
