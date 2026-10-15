"""Round 9 comparison pre-pass: durations judged in days, totals settled from the price lines.

R3-A04-T2 (flash): compare-check read 「납기 2주」 as 14 against a 「1달」 limit — 「납기 14달 >
조건 1달」, 다솜시스템 탈락 — and 「납기 계약 후 3주」 as no value, so 가온정보통신 was 조건 미확인.
The reading's numbers carry no unit; the material's words do.

R3-A04-T1 (27B, flash) and R3-A04-T2: compare-cost readings split three ways (B안 8,712,000 /
9,720,080 / 9,720,000), a thinking read ran up to its 360 s cap, and the report took 500-740 s
with wrong B안 figures and working sentences in the body. The price lines are written plainly;
code reads them and settles the totals when the readings differ.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.services import report, report_checks

#: the tasks as sent (tasks/R3-A04-T1/prompt.md, tasks/R3-A04-T2/prompt.md)
T1 = """우리 팀(교직원 40명) 업무 협업 도구 두 가지 안을 비교·검토하는 보고서 써 줘. 결론적으로 어느 안을 추천하는지까지.

A안: 지금 쓰는 사내 메신저(무료) 유지 + 화상회의 라이선스만 추가 구매
- 화상회의 라이선스 1인당 월 8,000원 × 40명
- 문서 공동편집 불가(지금처럼 파일 메일 첨부), 일정·할 일 관리 기능 없음
- 교육 필요 없음, 전환 작업 없음

B안: 통합 협업툴(메신저·화상회의·공동편집·일정·할 일) 전환
- 1인당 월 14,000원 × 40명, 연 단위 계약 시 15% 할인
- 초기 전환 컨설팅·데이터 이전 1회 300만원
- 전 직원 교육 2시간 1회 필요
- 외부 클라우드 저장(국내 리전), 정보보호 인증(ISMS) 보유
- 지난 분기 설문에서 '파일 버전 혼선' 불만이 응답자 35명 중 21명

판단 기준: 연간 비용(첫해·이듬해), 업무 효율, 보안, 도입 부담."""  # noqa: E501

T2 = """교직원 업무용 노트북 30대 교체 건, 견적 세 곳 받은 걸로 비교 검토 보고서 써 줘. 어디로 할지 추천까지.
(아래는 우리 팀이 받은 견적 자료임 — 한빛대학교 정보전산팀)

[견적 1] 가온정보통신
- 모델: 14인치, CPU i5급, RAM 16GB, SSD 512GB
- 단가 1,320,000원(부가세 별도) × 30대
- 무상 보증 3년(방문 수리), 납기 계약 후 3주
- 기존 노트북 30대 수거·데이터 완전 삭제 무료

[견적 2] 다솜시스템
- 모델: 14인치, CPU i5급, RAM 16GB, SSD 512GB
- 단가 1,390,000원(부가세 포함) × 30대
- 무상 보증 2년(센터 입고), 3년 연장 시 대당 45,000원(부가세 포함)
- 납기 2주, 기존 기기 수거는 대당 10,000원(부가세 포함)

[견적 3] 누리컴
- 모델: 14인치, CPU i7급, RAM 16GB, SSD 1TB
- 단가 1,480,000원(부가세 별도) × 30대, 30대 이상 구매 시 총액 5% 할인
- 무상 보증 3년(방문 수리), 납기 5주
- 기존 기기 수거 무료, 데이터 삭제 확인서는 별도 대당 5,000원(부가세 별도)

조건: 예산 4,800만원(부가세 포함), 11월 30일까지 지급 완료돼야 함(오늘 10월 12일 계약 가정), 보증 3년 필수, 기존 기기 데이터 삭제 증빙 필요."""  # noqa: E501

TODAY = dt.date(2026, 10, 12)
NAMES = ["가온정보통신", "다솜시스템", "누리컴"]


# ── 1. durations in days ──────────────────────────────────────────────────

@pytest.mark.parametrize("text, days", [
    ("납기 2주", (14, 14)), ("납기 계약 후 3주", (21, 21)), ("6개월", (180, 180)),
    ("2달", (60, 60)), ("10일", (10, 10)), ("1년", (365, 365)), ("15영업일", (21, 21)),
    ("1~4주", (7, 28)), ("2 ~ 3개월", (60, 90)), ("1주~2개월", (7, 60)),
])
def test_a_duration_is_read_in_days_with_its_unit(text, days):
    found = report_checks.durations(text)
    assert len(found) == 1 and (found[0]["lo"], found[0]["hi"]) == days


def test_a_day_of_a_month_and_a_count_beside_a_duration_are_not_durations():
    # 「11월 30일」 is a date; 「14인치」「30대」 are no durations; 「3년차」 is an ordinal
    assert report_checks.durations("11월 30일까지, 14인치, 30대, 3년차") == []
    # a number next to a duration is not joined to it (「30대 2주」 is 2주)
    found = report_checks.durations("30대 2주, 1,320,000원 3주")
    assert [d["text"] for d in found] == ["2주", "3주"]


def test_a_deadline_counted_from_the_contract_day_is_the_lead_time_limit():
    # the reading's 「1달」 is never the limit; 10월 12일 → 11월 30일 is
    must = {"name": "납기", "kind": "max", "limit": 1, "unit": "달"}
    limit = report_checks.duration_limit(must, T2, TODAY)
    assert limit["days"] == 49 and limit["text"] == "49일(10월 12일~11월 30일)"
    # a limit written with its unit stands as written
    weeks = {"name": "납기", "kind": "max", "limit": 4, "unit": "주"}
    assert report_checks.duration_limit(weeks, "납기 4주 이내 필수")["days"] == 28
    # nothing in the text settles it: no limit
    assert report_checks.duration_limit(must, "납기는 빠를수록 좋다") is None


@pytest.mark.parametrize("value, verdict", [
    ("1~4주", "met"), ("5~8주", "open"), ("8~9주", "failed"), ("7주", "met"), ("8주", "failed"),
])
def test_a_range_across_the_limit_is_open(value, verdict):
    limit = {"days": 49.0, "rough": False, "unit": "일", "text": "49일"}
    d = report_checks.durations(value)[0]
    assert report_checks.duration_verdict(d, limit, "max") == verdict


def _verdict(text: str, limit: dict, kind: str) -> str:
    return report_checks.duration_verdict(report_checks.durations(text)[0], limit, kind)


def test_the_same_unit_compares_exactly():
    limit = report_checks.duration_limit({"name": "보증", "kind": "min", "limit": 3, "unit": "년"},
                                         "보증 3년 필수")
    assert limit["months"] == 36
    assert _verdict("3년", limit, "min") == "met"
    assert _verdict("2년", limit, "min") == "failed"
    # months and years compare in months: 36개월 is 3년, 35개월 is short of it
    assert _verdict("36개월", limit, "min") == "met"
    assert _verdict("35개월", limit, "min") == "failed"
    # 1달 against 30일 is too close to call in days
    days = {"days": 30.0, "rough": False, "unit": "일", "text": "30일"}
    assert _verdict("1달", days, "max") == "open"


# ── 2. compare-check on R3-A04-T2 ─────────────────────────────────────────

#: the flash reading behind 「납기 14달 > 조건 1달」: weeks turned into days, limit in months
FLASH_READING = {
    "criteria": [],
    "musts": [{"name": "납기", "kind": "max", "limit": 1, "unit": "달"},
              {"name": "무상 보증", "kind": "min", "limit": 3, "unit": "년"},
              {"name": "데이터 삭제 증빙", "kind": "yesno"},
              {"name": "예산", "kind": "max", "limit": 48000000, "unit": "원",
               "vat_basis": "incl"}],
    "options": [
        {"name": "가온정보통신", "musts": {"납기": 21, "무상 보증": 3, "데이터 삭제 증빙": "있음",
                                     "예산": None}},
        {"name": "다솜시스템", "musts": {"납기": 14, "무상 보증": 2, "데이터 삭제 증빙": "있음",
                                    "예산": None}},
        {"name": "누리컴", "musts": {"납기": 35, "무상 보증": 3, "데이터 삭제 증빙": "있음",
                                 "예산": None}}]}


def _given() -> str:
    return f"{T2}\n{report._request_material(T2)}"


def _code_costs() -> dict:
    given = _given()
    data = report_checks.price_lines_reading(T2, report._request_material(T2))
    return report_checks.option_costs(report_checks.counted_as_written(data, given),
                                      report_checks._material_numbers(given), material=given)


def test_lead_times_are_judged_in_days_from_the_material_s_words():
    given = _given()
    result = report_checks.must_status(FLASH_READING, report_checks._material_numbers(given),
                                       given, _code_costs(), today=TODAY)
    rows = result["options"]
    block = report_checks._must_block(result)
    assert "14달" not in block and "1달" not in block
    assert rows["가온정보통신"]["status"] == "충족"
    assert rows["누리컴"]["status"] == "충족"
    # 다솜: 2주 meets the deadline; its warranty meets 3년 by the extension it sells; its quote
    # says nothing of a deletion certificate, so a reading of 「있음」 is no verdict
    assert rows["다솜시스템"] == {"status": "조건 미확인", "failed": [],
                              "unknown": ["데이터 삭제 증빙"],
                              "conditional": ["무상 보증 3년 연장 시 충족(연장 비용 추가)"]}
    assert ("- 다솜시스템: 조건 미확인 — 데이터 삭제 증빙 자료 없음 "
            "(무상 보증 3년 연장 시 충족(연장 비용 추가))") in block


def test_a_lead_time_past_the_deadline_is_out_with_both_in_days():
    late = T2.replace("납기 5주", "납기 8주")
    result = report_checks.must_status(FLASH_READING, report_checks._material_numbers(late),
                                       late, today=TODAY)
    assert result["options"]["누리컴"]["failed"] == [
        "납기 8주(56일) > 기한 49일(10월 12일~11월 30일)"]


def test_a_lead_time_no_text_bounds_is_not_judged_and_said_so():
    loose = T2.replace("11월 30일까지 지급 완료돼야 함(오늘 10월 12일 계약 가정), ", "")
    result = report_checks.must_status(FLASH_READING, report_checks._material_numbers(loose),
                                       loose, today=TODAY)
    assert result["musts"] == ["무상 보증", "데이터 삭제 증빙", "예산"]
    assert result["unjudged"] == ["납기"]
    assert "Not judged: 납기" in report_checks._must_block(result)


def test_a_duration_the_material_does_not_write_for_an_option_is_open():
    bare = T2.replace(", 납기 계약 후 3주", "")
    result = report_checks.must_status(FLASH_READING, report_checks._material_numbers(bare),
                                       bare, today=TODAY)
    assert "납기" in result["options"]["가온정보통신"]["unknown"]


# ── 3. compare-cost: the price lines read by code ─────────────────────────

def test_the_t1_price_lines_settle_both_years_and_the_contract_discount_as_a_scenario():
    material = report._request_material(T1)
    given = f"{T1}\n{material}"
    data = report_checks.price_lines_reading(T1, material)
    costs = report_checks.option_costs(
        report_checks.counted_as_written(data, given),
        report_checks._material_numbers(given) + report_checks._calendar(given), material=given)
    base = {n: (e["total"], e["first_year"], e["next_year"]) for n, e in costs["options"].items()}
    assert base == {"A안": (3_840_000, 3_840_000, 3_840_000),
                    "B안": (9_720_000, 9_720_000, 6_720_000)}
    # the buyer's choice stays a scenario (r8): 연 단위 계약 시 8,712,000 / 5,712,000
    (scenario,) = costs["scenarios"]
    assert scenario["name"] == "연 단위 계약 시"
    assert scenario["options"]["B안"]["total"] == 8_712_000
    assert scenario["options"]["B안"]["next_year"] == 5_712_000


def test_the_t2_price_lines_settle_every_quote():
    costs = _code_costs()
    assert {n: e["total"] for n, e in costs["options"].items()} == {
        "가온정보통신": 43_560_000, "다솜시스템": 43_350_000, "누리컴": 46_563_000}
    # 30대 이상 is met by the order; the extension is named, not taken for the laptops' price
    assert costs["applied"] == ["30대 이상 구매 시 (누리컴)"]
    rows = [r["name"] for r in costs["options"]["다솜시스템"]["rows"]]
    assert rows == ["구입가", "기존 기기 수거", "무상 보증 3년 연장"]
    assert costs["budget"]["room"] == {"가온정보통신": 4_440_000, "다솜시스템": 4_650_000,
                                       "누리컴": 1_437_000}


@pytest.mark.parametrize("line", [
    "- 화상회의 시간당 2,000원, 월 평균 30시간",          # a usage price
    "- 1인당 월 14,000원 또는 연 150,000원",               # two amounts in one clause
    "- 총액 5% 할인",                                       # a discount beside no price
])
def test_a_line_code_cannot_read_leaves_the_reading_to_the_model(line):
    text = T1.replace("- 전 직원 교육 2시간 1회 필요", line)
    assert report_checks.price_lines_reading(text, report._request_material(text)) is None


def test_an_amount_outside_the_options_leaves_the_reading_to_the_model():
    text = T1 + "\n공통: 설치비 50만원"
    assert report_checks.price_lines_reading(text, "") is None


def _t1_reading(b_items: list[dict]) -> dict:
    return {"period": "1년", "years": 1, "options": [
        {"name": "A안", "items": [{"name": "화상회의", "unit_price": 8000, "count": 40,
                                  "per": "month"}]},
        {"name": "B안", "items": b_items}], "scenarios": []}


#: the r9 L27F readings: discount taken unconditionally / a stray 80 won / discount as a condition
UNCONDITIONAL = _t1_reading([
    {"name": "이용료", "unit_price": 14000, "count": 40, "per": "month"},
    {"name": "할인", "unit_price": 14000, "count": 40, "per": "month", "rate": 0.15, "sign": -1},
    {"name": "컨설팅", "unit_price": 3000000, "count": 1, "per": "once"}])
STRAY = _t1_reading([
    {"name": "이용료", "unit_price": 14000, "count": 40, "per": "month"},
    {"name": "교육", "unit_price": 40, "count": 2, "per": "once"},
    {"name": "컨설팅", "unit_price": 3000000, "count": 1, "per": "once"}])
NO_SCENARIO = _t1_reading([
    {"name": "이용료", "unit_price": 14000, "count": 40, "per": "month"},
    {"name": "컨설팅", "unit_price": 3000000, "count": 1, "per": "once"}])


def _replies(*replies: dict, check: dict | None = None):
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = messages[0]["content"]
        if "computing each alternative's total cost" in prompt:
            asked.append(model)
            return json.dumps(replies[len(asked) - 1], ensure_ascii=False), {}
        if "scoring the alternatives and checking the must-have" in prompt:
            return json.dumps(check or {"criteria": [], "musts": [], "options": []},
                              ensure_ascii=False), {}
        return "{}", {}

    async def deliberate(*args, **kwargs):
        raise AssertionError("no thinking read when code reads the price lines")

    return complete, asked, deliberate


@pytest.mark.parametrize("strong", [False, True])
async def test_readings_that_split_are_settled_by_the_price_lines_without_a_thinking_read(
        strong, monkeypatch):
    monkeypatch.setattr(report_checks.thinking, "thinks_by_default", lambda m: True)
    complete, asked, deliberate = _replies(UNCONDITIONAL, STRAY)
    pre = await report_checks.prepare(
        "comparison", T1, report._request_material(T1), complete=complete,
        model="local/qwen3.8-flash", api_key="k", tiebreak_model="local/qwen3.8-flash",
        deliberate=deliberate, strong=strong)
    assert len(asked) == 2  # the two readings only; no third model call
    run = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert not run.get("error") and not pre.costs_withheld
    assert run["summary"].startswith("총비용 2개 대안(1년): A안 3,840,000원, B안 9,720,000원")
    assert run["summary"].endswith("읽을 때마다 값이 달라 자료의 가격 줄을 코드가 읽어 계산")
    assert pre.costs["scenarios"][0]["options"]["B안"]["total"] == 8_712_000
    block = next(b for b in pre.blocks if b.startswith("# 총비용 계산(코드)"))
    assert "9,720,080" not in block and "8,712,000" in block
    # nothing marked as differing for any group: the scenario is priced from the line itself
    assert "읽을 때마다 값이 다름" not in block


async def test_a_reading_agreeing_with_the_price_lines_takes_the_code_s_scenarios():
    # r9 LFLF A04-T1: two readings agreed on 9,720,000 but one priced the 연 단위 계약
    # scenario and the other did not — 「총비용 일부 보류 — 읽을 때마다 다른 할인 조건: B안」
    complete, asked, _ = _replies(NO_SCENARIO, NO_SCENARIO)
    pre = await report_checks.prepare("comparison", T1, report._request_material(T1),
                                      complete=complete, model="m", api_key="k")
    assert not pre.costs_withheld
    assert pre.costs["options"]["B안"]["total"] == 9_720_000
    assert [sc["name"] for sc in pre.costs["scenarios"]] == ["연 단위 계약 시"]


def _t2_reading(extension: bool, discount_when: str | None) -> dict:
    discount = {"name": "할인", "unit_price": 1480000, "count": 30, "per": "once",
                "rate": 0.05, "sign": -1, "vat": "excl"}
    if discount_when:
        discount["when"] = discount_when
    dasom = [{"name": "노트북", "unit_price": 1390000, "count": 30, "per": "once", "vat": "incl"},
             {"name": "수거", "unit_price": 10000, "count": 30, "per": "once", "vat": "incl"}]
    if extension:
        dasom.append({"name": "보증 연장", "unit_price": 45000, "count": 30, "per": "once",
                      "vat": "incl", "when": "3년 연장 시"})
    return {"period": "", "options": [
        {"name": "가온정보통신", "items": [
            {"name": "노트북", "unit_price": 1320000, "count": 30, "per": "once", "vat": "excl"}]},
        {"name": "다솜시스템", "items": dasom},
        {"name": "누리컴", "items": [
            {"name": "노트북", "unit_price": 1480000, "count": 30, "per": "once", "vat": "excl"},
            discount,
            {"name": "확인서", "unit_price": 5000, "count": 30, "per": "once", "vat": "excl"}]}],
        "scenarios": []}


async def test_t2_settles_every_quote_and_judges_lead_times_in_days():
    # one reading left out 다솜's extension, the other wrote 누리컴's discount as another
    # condition: no two agree, and before this 다솜시스템·누리컴 were held back
    complete, asked, _ = _replies(_t2_reading(False, "30대 이상 구매 시"),
                                  _t2_reading(True, "총액 할인 시"), check=FLASH_READING)
    pre = await report_checks.prepare("comparison", T2, report._request_material(T2),
                                      complete=complete, model="m", api_key="k", today=TODAY)
    assert not pre.costs_withheld
    assert {n: e["total"] for n, e in pre.costs["options"].items()} == {
        "가온정보통신": 43_560_000, "다솜시스템": 43_350_000, "누리컴": 46_563_000}
    check = next(r for r in pre.runs if r["skill"] == "compare-check")
    assert check["summary"] == "필수 조건 가온정보통신 충족, 다솜시스템 조건 미확인, 누리컴 충족"
    block = next(b for b in pre.blocks if b.startswith("# 필수 조건 판정(코드)"))
    assert "14달" not in block and "납기" not in block.split("\n", 2)[2]


async def test_readings_that_agree_against_the_price_lines_stand():
    # the code's reading is a vote, not a veto: two agreeing readings are kept
    other = json.loads(json.dumps(UNCONDITIONAL))
    complete, _, _ = _replies(other, other)
    pre = await report_checks.prepare("comparison", T1, report._request_material(T1),
                                      complete=complete, model="m", api_key="k")
    assert pre.costs["options"]["B안"]["total"] == 8_712_000
