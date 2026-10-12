"""Round-2 R2W-07: the verify2 GPU decision. A hourly price for 「A100 80GB 4장」 is the whole
instance's — the cost reading's count of four is not the material's and is set back to one
(A = 24,000,000원/년, 72,000,000원 over 3 years). The yearly money limit is judged on that
code-computed yearly cost, a cloud option fails 「데이터 외부 반출 불가」, and a written
product with units in every term (「12,000원 × 2,000시간 × 3년 = 6,000원」) is recomputed
(sc/v_gpu)."""

from __future__ import annotations

import json

from app.services import report, report_checks, units

#: the persona's request, every figure inline (sc/v_gpu/spec.json)
GPU = ("연구실 GPU 연산 자원 도입 안을 비교해서 결정 보고서로 써 줘. A안: 클라우드 GPU(A100 80GB "
       "4장) 시간당 12,000원, 연간 예상 사용 2,000시간, 초기비용 없음. B안: 온프레미스 서버(A100 "
       "80GB 4장) 구매가 1억 2천만원, 연간 전기·유지비 900만원, 내용연수 5년. 필수 조건: "
       "연구비 연간 집행 한도 3,000만원 이내, 데이터 외부 반출 불가(민감 데이터 포함). "
       "3년 총비용과 필수 조건 충족 여부로 권고해 줘.")

#: the reading that settled 96,000,000원 a year for A: 12,000 × 4장 × 2,000
COUNTED_FOUR = {"period": "3년", "years": 3, "options": [
    {"name": "A안", "items": [{"name": "사용료", "unit_price": 12000, "count": 4, "per": "시간",
                               "usage": 2000, "years": 3}]},
    {"name": "B안", "items": [
        {"name": "구매가", "unit_price": 120000000, "count": 1, "per": "once"},
        {"name": "전기·유지비", "unit_price": 9000000, "count": 1, "per": "year", "years": 3}]}],
    "scenarios": []}

#: the must reading as shipped: A passes 반출 and the limit is left to the costs
MUSTS = {"criteria": [], "musts": [
    {"name": "연구비 연간 집행 한도", "kind": "max", "limit": 30000000, "unit": "원"},
    {"name": "데이터 외부 반출 불가", "kind": "yesno"}],
    "options": [
        {"name": "A안", "values": {}, "musts": {"연구비 연간 집행 한도": 12000,
                                               "데이터 외부 반출 불가": "있음"}},
        {"name": "B안", "values": {}, "musts": {"데이터 외부 반출 불가": "있음"}}]}


def _answering(cost: dict, check: dict):
    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = messages[0]["content"]
        if "computing each alternative's total cost" in prompt:
            return json.dumps(cost, ensure_ascii=False), {}
        if "scoring the alternatives and checking the must-have" in prompt:
            return json.dumps(check, ensure_ascii=False), {}
        return "## 요약\n비교했다.", {"inputTokens": 1, "outputTokens": 1}
    return complete


def test_a_count_the_material_does_not_price_per_unit_is_one():
    material = report._request_material(GPU)
    assert not report_checks.priced_per_unit(4, material)
    # a per-unit price, a count of usage, an unwritten count stand
    assert report_checks.priced_per_unit(3, "노트북 3대, 대당 250만 원")
    assert report_checks.priced_per_unit(40, "화상회의 라이선스 1인당 월 8,000원 × 40명")
    assert report_checks.priced_per_unit(80, "1인 월 6,000원, 직원 80명, 3년 계약")
    assert report_checks.priced_per_unit(2000, material)
    assert report_checks.priced_per_unit(5, "차량 운영 5년, 월 89만 원")


async def test_the_gpu_pre_pass_settles_the_instance_price_and_both_musts():
    pre = await report_checks.prepare(
        "comparison", GPU, report._request_material(GPU),
        complete=_answering(COUNTED_FOUR, MUSTS), model="m", api_key="k")
    totals = {name: entry["total"] for name, entry in pre.costs["options"].items()}
    assert totals == {"A안": 72_000_000, "B안": 147_000_000}
    a, b = pre.musts["options"]["A안"], pre.musts["options"]["B안"]
    # A: 24,000,000원 a year is within 30,000,000원; cloud fails the residency must
    assert a["status"] == "탈락"
    assert a["failed"] == ["데이터 외부 반출 불가 아님(클라우드라 데이터가 외부 서버에 있음)"]
    # B: 129,000,000원 in its first year is over the limit
    assert b["status"] == "탈락"
    assert b["failed"] == ["연구비 연간 집행 한도 129,000,000원 > 조건 30,000,000원"]


def test_a_written_product_with_units_in_every_term_is_recomputed():
    row = ("| 3년 총비용(계산식) | 12,000원 × 2,000시간 × 3년 = 6,000원 | "
           "120,000,000원 + 9,000,000원 × 3년 = 27,000,000원 |")
    fixed = units.fix_written_sums(units.fix_written_arithmetic(row))
    assert fixed == ("| 3년 총비용(계산식) | 12,000원 × 2,000시간 × 3년 = 72,000,000원 | "
                     "120,000,000원 + 9,000,000원 × 3년 = 147,000,000원 |")
    # the tail of a product whose head is not an amount is not judged as a product
    assert units.fix_written_arithmetic("12달러 × 2,000시간 × 3년 = 72,000원") \
        == "12달러 × 2,000시간 × 3년 = 72,000원"
