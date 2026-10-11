"""comparison-report-5: options priced by parts settle their 5-year totals before writing.

Both runs had no pre-pass step at all: the pack has no 「단가」 or 「×」 line, so quote-calc
never ran, and without weights or must-haves nothing else did. r1 wrote 213,500,000 /
277,500,000 / 197,050,000 against 197,900,000 / 282,000,000 / 296,250,000.
"""

from __future__ import annotations

import json

from app.services import report_checks

PACK = "## compare_fleet_pack.md\n" + """# 업무차량 5대 운영 방식 비교 — 총무팀 자료 모음

## [자료 1] 차량·운영 가정(총무팀)
- 차종: 전기 SUV 「EV-M」 5대, 차량가 대당 48,000,000원
- 운영 기간 5년, 대당 연 주행 15,000km(현재 추정) — 영업팀은 내년부터 25,000km까지 늘 수 있다고 함
- 전기료: km당 40원
- 구매 시: 취득세 등(감면 후) 대당 1,400,000원, 보험 대당 연 1,200,000원, 정비 대당 연 300,000원, 5년 뒤 잔존가치 차량가의 35%
- 장기렌트: 대당 월 890,000원(보험·정비 포함, 60개월), 연 20,000km 초과 시 km당 120원, 전기료 별도
- 법인 카셰어링: 시간당 9,000원 + 주행 km당 190원(전기료 포함), 대당 연 1,000시간 사용 예상

## [자료 2] 블로그 「2025 전기차 보조금 총정리」(2025-02)
- EV-M 보조금(국비+지자체) 1,050만 원

## [자료 3] 시 공고 「2026년 전기자동차 구매 지원사업」(2026-02)
- EV-M 지원액(국비+지자체) 880만 원
- 법인·기관은 같은 해 2대까지만 지원

## [자료 4] 재무팀 메모(2026-09)
- 2027년 자본 예산 편성 완료 — 차량 구매(자본 지출)는 2027년 안에 불가, 2028년 이후 검토
- 차량 운영비(비용 처리)는 연 60,000,000원 한도
"""  # noqa: E501 — the pack as attached
REQUEST = ("업무차량 5대 운영 방식(구매·장기렌트·카셰어링) 비교 보고서 써 줘. 자료 모은 거 붙였어. "
           "5년 총비용으로 비교하고, 주행 거리가 늘면 결론이 바뀌는지도 봐 줘. 경영회의 안건이야.")
#: A reply shaped the way the prompt asks, from the pack's own numbers.
REPLY = {
    "period": "5년",
    "options": [
        {"name": "구매", "items": [
            {"name": "차량가", "factors": [5, 48000000]},
            {"name": "보조금(2026 공고, 2대 한도)", "factors": [2, 8800000], "sign": -1},
            {"name": "취득세 등", "factors": [5, 1400000]},
            {"name": "보험", "factors": [5, 1200000, 5]},
            {"name": "정비", "factors": [5, 300000, 5]},
            {"name": "전기료", "factors": [5, 15000, 40, 5]},
            {"name": "잔존가치", "factors": [5, 48000000, 0.35], "sign": -1}]},
        {"name": "장기렌트", "items": [
            {"name": "렌트료", "factors": [5, 890000, 60]},
            {"name": "전기료", "factors": [5, 15000, 40, 5]},
            {"name": "초과 주행료", "factors": [5, 120, 5], "over": [15000, 20000]}]},
        {"name": "카셰어링", "items": [
            {"name": "시간 요금", "factors": [5, 1000, 9000, 5]},
            {"name": "주행 요금", "factors": [5, 15000, 190, 5]}]},
    ],
    "scenarios": [{"name": "연 25,000km", "replace": [{"from": 15000, "to": 25000}]}],
}


def test_each_option_s_total_is_the_product_of_the_pack_s_numbers():
    numbers = report_checks._material_numbers(f"{REQUEST}\n{PACK}")
    costs = report_checks.option_costs(REPLY, numbers)
    totals = {name: entry["total"] for name, entry in costs["options"].items()}
    assert totals == {"구매": 197_900_000, "장기렌트": 282_000_000, "카셰어링": 296_250_000}
    assert costs["lowest"] == "구매" and costs["dropped"] == []
    far = {n: e["total"] for n, e in costs["scenarios"][0]["options"].items()}
    # answers.py v5(25_000): 구매 207,900,000 · 장기렌트 307,000,000 · 카셰어링 343,750,000.
    assert far == {"구매": 207_900_000, "장기렌트": 307_000_000, "카셰어링": 343_750_000}


def test_a_factor_the_pack_does_not_state_is_left_out_and_named():
    numbers = report_checks._material_numbers(PACK)
    made_up = {"options": [
        {"name": "구매", "items": [{"name": "차량가", "factors": [5, 48000000]},
                                 {"name": "보조금", "factors": [5, 10500000, 0.9], "sign": -1}]},
        {"name": "장기렌트", "items": [{"name": "렌트료", "factors": [5, 890000, 60]}]},
    ]}
    costs = report_checks.option_costs(made_up, numbers)
    assert costs["dropped"] == ["구매 보조금"]
    assert costs["options"]["구매"]["total"] == 240_000_000


async def test_the_pre_pass_runs_without_weights_and_says_so():
    calls = []

    async def complete(model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        calls.append(prompt)
        if "computing each alternative's total cost" in prompt:
            return json.dumps(REPLY, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        return json.dumps({"criteria": [], "musts": [], "options": []}), {}

    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model="m", api_key="k")
    run = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert run["summary"] == ("총비용 3개 대안(5년): 구매 197,900,000원, 장기렌트 282,000,000원, "
                              "카셰어링 296,250,000원 · 시나리오 1개")
    block = next(b for b in pre.blocks if b.startswith("# 총비용 계산(코드)"))
    assert "| 구매 | 잔존가치 | 5 × 48,000,000 × 0.35 | -84,000,000 |" in block
    assert "- 시나리오 「연 25,000km」: 구매 207,900,000원" in block


# ── B9: the pre-pass that came back empty said nothing (lineup v1, deploy 3) ──

#: What the 27B model answered on the pack: the prompt's own example (then this very
#: pack) echoed back as one option, so no total was settled and no step was recorded.
ECHOED = {
    "period": "5년",
    "options": [{"name": "구매", "items": [
        {"name": "차량가", "factors": [5, 48000000]},
        {"name": "보조금", "factors": [2, 8800000], "sign": -1},
        {"name": "잔존가치", "factors": [5, 48000000, 0.35], "sign": -1},
        {"name": "초과 주행료", "factors": [5, 120, 5], "over": [15000, 20000]}]}],
    "scenarios": [{"name": "연 25,000km", "replace": [{"from": 15000, "to": 25000}]}],
}
#: comparison-report-1: the material pasted in the request, priced per person per month.
MESSENGER = (
    "사내 메신저 세 개 비교 보고서 써 줘. 직원 80명이고 SSO 연동은 필수, "
    "화상회의는 한 번에 60명까지 되면 돼. 연간 비용 기준으로 하나 골라 줘.\n\n"
    "[제품 정보 — 각 사 요금 페이지, 2026-10-01 확인]\n"
    "- 톡플로우: 1인 월 6,000원, SSO 지원, 국내 데이터센터, 화상회의 최대 100명\n"
    "- 팀링크: 기본 요금제 1인 월 4,500원(SSO 미지원) / 엔터프라이즈 1인 월 8,000원(SSO 지원), "
    "해외 데이터센터, 화상회의 최대 50명\n"
    "- 워크닷: 1인 월 7,500원, SSO 지원, 국내 데이터센터, 화상회의 최대 300명")
#: The 27B model's reply on it: 12 months a year, a number the material never writes.
MESSENGER_REPLY = {"period": "1년", "options": [
    {"name": "톡플로우", "items": [{"name": "구독료", "factors": [80, 6000, 12]}]},
    {"name": "팀링크", "items": [{"name": "구독료", "factors": [80, 8000, 12]}]},
    {"name": "워크닷", "items": [{"name": "구독료", "factors": [80, 7500, 12]}]}], "scenarios": []}


def _answering(cost: dict, check: dict | None = None):
    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = messages[0]["content"]
        if "computing each alternative's total cost" in prompt:
            return json.dumps(cost, ensure_ascii=False), {}
        if "scoring the alternatives and checking the must-have" in prompt:
            return json.dumps(check or {"criteria": [], "musts": [], "options": []}), {}
        return "## 요약\n비교했다.", {"inputTokens": 1, "outputTokens": 1}
    return complete


def test_the_cost_prompt_does_not_carry_a_task_s_own_numbers():
    # The example was the fleet pack itself; the model copied it back as the answer.
    assert "48000000" not in report_checks._COST_PROMPT
    assert "8800000" not in report_checks._COST_PROMPT


async def test_an_echoed_single_option_is_said_in_the_step_not_dropped_silently():
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=_answering(ECHOED),
                                      model="m", api_key="k")
    cost = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert cost["error"] and cost["summary"] == "총비용 계산 못 함 (비용을 셀 수 있는 대안 1개)"
    check = next(r for r in pre.runs if r["skill"] == "compare-check")
    assert check["skipped"] and "필수 조건과 가중치가 없어" in check["summary"]
    assert pre.costs is None and pre.blocks == []


async def test_a_yearly_cost_of_a_monthly_price_is_settled_from_the_request_s_material():
    from app.services import report

    material = report._request_material(MESSENGER)
    pre = await report_checks.prepare("comparison", MESSENGER, material,
                                      complete=_answering(MESSENGER_REPLY), model="m", api_key="k")
    cost = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert cost["summary"] == ("총비용 3개 대안(1년): 톡플로우 5,760,000원, 팀링크 7,680,000원, "
                               "워크닷 7,200,000원")
    # 12 counts only where monthly prices meet years.
    assert report_checks._calendar("1인 월 6,000원, 연간 비용") == [12.0]
    assert report_checks._calendar("연 18,000,000원, 300명") == []


async def test_a_comparison_whose_pre_pass_found_nothing_records_a_step(monkeypatch):
    from app.services import report

    monkeypatch.setattr(report, "_complete", _answering(ECHOED))
    events = [e async for e in report.write(
        request=REQUEST, model="m", api_key="k", web_search=False,
        untrusted_context=[PACK], template_id="doc-comparison",
        approved_plan={"title": "업무차량 운영 방식 비교", "sections": ["요약"]},
    )]
    step = next(e for e in events if e["type"] == "step" and e["id"] == "prepass")
    assert step["status"] == "error" and step["label"] == "사전 계산: compare-cost"
    assert "비용을 셀 수 있는 대안 1개" in step["detail"]


async def test_a_pre_pass_that_raises_records_a_failed_step(monkeypatch):
    from app.services import report

    async def boom(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(report, "_complete", _answering(REPLY))
    monkeypatch.setattr(report.report_checks, "prepare", boom)
    events = [e async for e in report.write(
        request=REQUEST, model="m", api_key="k", web_search=False,
        untrusted_context=[PACK], template_id="doc-comparison",
        approved_plan={"title": "업무차량 운영 방식 비교", "sections": ["요약"]},
    )]
    step = next(e for e in events if e["type"] == "step" and e["id"] == "prepass")
    assert step["status"] == "error" and "RuntimeError" in step["detail"]


# ── two readings must agree; a third by the larger model settles a split ──

def _wrong(purchase_items: int) -> dict:
    """The reply with the purchase cut short: 185,900,000-style misreadings."""
    reply = json.loads(json.dumps(REPLY))
    reply["options"][0]["items"] = reply["options"][0]["items"][:purchase_items]
    return reply


def _by_turn(*replies: dict):
    """Cost replies in order of asking; the models asked are kept."""
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        if "computing each alternative's total cost" in messages[0]["content"]:
            asked.append(model)
            return json.dumps(replies[len(asked) - 1], ensure_ascii=False), {}
        return json.dumps({"criteria": [], "musts": [], "options": []}), {}
    return complete, asked


async def test_two_readings_that_agree_settle_the_totals_without_a_third():
    complete, asked = _by_turn(REPLY, REPLY)
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model="m", api_key="k", tiebreak_model="big")
    assert asked == ["m", "m"]
    assert pre.costs["options"]["구매"]["total"] == 197_900_000


async def test_a_split_is_settled_by_the_larger_model_agreeing_with_one():
    complete, asked = _by_turn(_wrong(3), REPLY, REPLY)
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model="m", api_key="k", tiebreak_model="big")
    assert asked == ["m", "m", "big"]
    run = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert run["summary"].startswith("총비용 3개 대안(5년): 구매 197,900,000원")
    assert run["summary"].endswith("세 번 읽어 둘이 같은 값")


async def test_three_readings_that_all_differ_hand_the_writer_no_totals():
    complete, _ = _by_turn(_wrong(3), REPLY, _wrong(5))
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model="m", api_key="k", tiebreak_model="big")
    run = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert run["error"] and run["summary"].startswith("총비용 계산 불일치 — 직접 확인 필요 (")
    assert pre.costs is None and pre.costs_withheld
    assert not any(b.startswith("# 총비용 계산(코드)\n") for b in pre.blocks)
    withheld = next(b for b in pre.blocks if b.startswith("# 총비용 계산(코드): 확정 못 함"))
    assert "leave them as 「[금액]」" in withheld and "197,900,000" not in withheld


async def test_scenarios_that_differ_between_agreeing_readings_are_left_out():
    other = json.loads(json.dumps(REPLY))
    other["scenarios"] = [{"name": "연 20,000km", "replace": [{"from": 15000, "to": 20000}]}]
    complete, _ = _by_turn(REPLY, other)
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model="m", api_key="k", tiebreak_model="big")
    assert pre.costs["options"]["구매"]["total"] == 197_900_000
    assert pre.costs["scenarios"] == []
    assert pre.costs["dropped"] == ["시나리오(읽을 때마다 다름) 연 25,000km"]


async def test_readings_that_name_an_option_with_more_words_still_agree():
    other = json.loads(json.dumps(REPLY))
    other["options"][2]["name"] = "법인 카셰어링"
    complete, asked = _by_turn(REPLY, other)
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model="m", api_key="k", tiebreak_model="big")
    assert asked == ["m", "m"] and pre.costs["options"]["카셰어링"]["total"] == 296_250_000
    assert len(pre.costs["scenarios"]) == 1


def test_the_tiebreak_stays_on_the_turn_s_route(monkeypatch):
    from app.services import report

    monkeypatch.setattr(report.settings, "code_fallback_model", "local/qwen3.5-122b")
    assert report._tiebreak_model("strict-local/qwen3.8-27b") == "strict-local/qwen3.5-122b"
    assert report._tiebreak_model("local/qwen3.8-27b") == "local/qwen3.5-122b"
    monkeypatch.setattr(report.settings, "code_fallback_model", "openai/gpt-x")
    assert report._tiebreak_model("strict-local/qwen3.8-27b") == "strict-local/qwen3.8-27b"


async def test_a_split_drops_the_sandbox_s_totals_too(monkeypatch):
    from app.services import calc, report

    async def computed_values(*args, **kwargs):
        return "구매 5년 총비용 = 185900000 원", {"inputTokens": 0, "outputTokens": 0}, "done"

    complete, _ = _by_turn(_wrong(3), REPLY, _wrong(5))
    seen: list[str] = []

    async def writer(model, messages, api_key, max_tokens, **kwargs):
        prompt = messages[0]["content"]
        if ("computing each alternative's total cost" in prompt
                or "scoring the alternatives" in prompt):
            return await complete(model, messages, api_key, max_tokens)
        seen.append(json.dumps(messages, ensure_ascii=False))
        return "## 요약\n비교했다.", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(calc, "computed_values", computed_values)
    monkeypatch.setattr(report, "_complete", writer)
    events = [e async for e in report.write(
        request=REQUEST, model="m", api_key="k", web_search=False,
        untrusted_context=[PACK], template_id="doc-comparison",
        approved_plan={"title": "업무차량 운영 방식 비교", "sections": ["요약"]},
    )]
    step = next(e for e in events if e["type"] == "step" and e["id"] == "prepass")
    assert step["status"] == "error" and "총비용 계산 불일치" in step["detail"]
    assert seen and not any("185900000" in s for s in seen)
    assert any("[금액]" in s for s in seen)


# ── B10: named factor fields; code does the multiplying ──────────────────

#: The pack in the named-field schema: what each number is, never the product.
NAMED = {
    "period": "5년", "years": 5,
    "options": [
        {"name": "구매", "items": [
            {"name": "차량가", "unit_price": 48000000, "count": 5, "per": "once"},
            {"name": "보조금(2026 공고, 2대 "
                     "한도)", "unit_price": 8800000, "count": 2, "per": "once",
             "sign": -1},
            {"name": "취득세 등", "unit_price": 1400000, "count": 5, "per": "once"},
            {"name": "보험", "unit_price": 1200000, "count": 5, "per": "year"},
            {"name": "정비", "unit_price": 300000, "count": 5, "per": "year", "years": 5},
            {"name": "전기료", "unit_price": 40, "count": 5, "per": "km", "usage": "15,000km"},
            {"name": "잔존가치", "unit_price": 48000000, "count": 5, "per": "once", "rate": 0.35,
             "sign": -1}]},
        {"name": "장기렌트", "items": [
            {"name": "렌트료", "unit_price": 890000, "count": 5, "per": "month", "months": 60},
            {"name": "전기료", "unit_price": 40, "count": 5, "per": "km", "usage": 15000},
            {"name": "초과 주행료", "unit_price": 120, "count": 5, "per": "km", "usage": 15000,
             "threshold": 20000}]},
        {"name": "카셰어링", "items": [
            {"name": "시간 요금", "unit_price": 9000, "count": 5, "per": "시간", "usage": 1000},
            {"name": "주행 요금", "unit_price": 190, "count": 5, "per": "km", "usage": 15000}]},
    ],
    "scenarios": [{"name": "연 25,000km", "replace": [{"from": 15000, "to": 25000}]}],
}


def test_named_fields_price_the_pack_with_code_doing_the_products():
    numbers = report_checks._material_numbers(f"{REQUEST}\n{PACK}")
    costs = report_checks.option_costs(NAMED, numbers)
    assert {n: e["total"] for n, e in costs["options"].items()} == {
        "구매": 197_900_000, "장기렌트": 282_000_000, "카셰어링": 296_250_000}
    far = {n: e["total"] for n, e in costs["scenarios"][0]["options"].items()}
    assert far == {"구매": 207_900_000, "장기렌트": 307_000_000, "카셰어링": 343_750_000}
    rows = {r["name"]: r for r in costs["options"]["카셰어링"]["rows"]}
    # r2's 45,004,750: the hours term and ×5 vehicles lost. Here both are fields.
    assert rows["시간 요금"]["amount"] == 225_000_000
    assert rows["주행 요금"]["amount"] == 71_250_000
    excess = next(r for r in costs["options"]["장기렌트"]["rows"] if r["name"] == "초과 주행료")
    assert excess["amount"] == 0 and excess["over"] == [15000, 20000]


def test_a_usage_price_with_no_usage_is_left_out_and_named():
    numbers = report_checks._material_numbers(PACK)
    short = json.loads(json.dumps(NAMED))
    del short["options"][2]["items"][0]["usage"]
    costs = report_checks.option_costs(short, numbers)
    assert "카셰어링 시간 요금" in costs["dropped"]


def test_a_monthly_price_over_years_takes_twelve_from_code_not_the_model():
    numbers = report_checks._material_numbers("1인 월 6,000원, 직원 80명, 3년 계약")
    reply = {"years": 3, "options": [
        {"name": "가", "items": [
            {"name": "구독", "unit_price": 6000, "count": 80, "per": "month"}]},
        {"name": "나", "items": [{"name": "구독", "unit_price": 6000, "count": 80, "per": "month",
                                  "months": 36}]}]}
    costs = report_checks.option_costs(reply, numbers)
    # 36 is not in the text: 「나」 is dropped; 「가」's 12 is the calendar's.
    assert costs is None or costs["options"]["가"]["total"] == 17_280_000
    reply["options"][1]["items"][0].pop("months")
    costs = report_checks.option_costs(reply, numbers)
    assert costs["options"]["가"]["total"] == 17_280_000 == costs["options"]["나"]["total"]


#: Research lane R-03: every figure inline in one paragraph, no price line, nothing attached.
GPU = ("연구실 GPU 연산 자원 도입 안을 비교해서 결정 보고서로 써 줘. A안: 클라우드 GPU(A100 80GB "
       "4장) 시간당 12,000원, 연간 예상 사용 2,000시간, 초기비용 없음. B안: 온프레미스 서버(A100 "
       "80GB 4장) 구매가 1억 2천만원, 연간 전기·유지비 900만원, 내용연수 5년. 필수 조건: "
       "연구비 연간 집행 한도 3,000만원 이내, 데이터 외부 반출 불가(민감 데이터 포함). "
       "3년 총비용과 필수 조건 충족 여부로 권고해 줘.")


def test_figures_written_inline_in_the_request_are_its_material():
    from app.services import report

    material = report._request_material(GPU)
    assert material.startswith("# 요청에 적은 자료\n")
    for figure in ("시간당 12,000원", "1억 2천만원", "900만원", "3,000만원"):
        assert figure in material
    assert "결정 보고서로 써 줘" not in material
    # One amount is not material to settle.
    assert report._request_material("예산 500만원 안에서 행사 기획안 써 줘.") == ""


async def test_the_cost_pre_pass_totals_inline_figures_by_code():
    from app.services import report

    reply = {"period": "3년", "years": 3, "options": [
        {"name": "A안", "items": [{"name": "사용료", "unit_price": 12000, "count": 2000,
                                   "per": "year"}]},
        {"name": "B안", "items": [
            {"name": "구매가", "unit_price": 120000000, "count": 1, "per": "once"},
            {"name": "전기·유지비", "unit_price": 9000000, "count": 1, "per": "year"}]}],
        "scenarios": []}
    pre = await report_checks.prepare(
        "comparison", GPU, report._request_material(GPU), complete=_answering(reply),
        model="m", api_key="k")
    cost = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert "72,000,000원" in cost["summary"] and "147,000,000원" in cost["summary"]


def test_a_kpi_tile_groups_a_long_amount():
    from app.services import report

    text = "본문.\n\n```kpi\n72000000원 | A안 3년 총비용\n2026년 | 도입 연도\n```"
    out = report._grounded_figures(text, True)
    assert "72,000,000원 | A안 3년 총비용" in out and "2026년 | 도입 연도" in out


#: Quality lane Q14 / built-in tuning S10: a conditional discount and a one-time cost.
COLLAB = ("우리 팀(교직원 40명) 업무 협업 도구 두 가지 안을 비교·검토하는 보고서 써 줘.\n\n"
          "A안: 사내 메신저 유지 + 화상회의 라이선스 추가\n"
          "- 화상회의 라이선스 1인당 월 8,000원 × 40명\n\n"
          "B안: 통합 협업툴 전환\n- 1인당 월 14,000원 × 40명, 연 단위 계약 시 15% 할인\n"
          "- 초기 전환 컨설팅·데이터 이전 1회 300만원\n\n판단 기준: 연간 비용(첫해·이듬해)")
COLLAB_REPLY = {"period": "1년", "years": 1, "options": [
    {"name": "A안", "items": [
        {"name": "화상회의 라이선스", "unit_price": 8000, "count": 40, "per": "month"}]},
    {"name": "B안", "items": [
        {"name": "구독료", "unit_price": 14000, "count": 40, "per": "month"},
        {"name": "연 단위 계약 할인", "unit_price": 14000, "count": 40, "per": "month",
         "rate": 0.15, "sign": -1, "when": "연 단위 계약 시"},
        {"name": "전환 컨설팅", "unit_price": 3000000, "count": 1, "per": "once"}]}]}


def test_a_conditional_discount_is_priced_with_and_without_it():
    numbers = report_checks._material_numbers(COLLAB) + report_checks._calendar(COLLAB)
    costs = report_checks.option_costs(COLLAB_REPLY, numbers)
    base = costs["options"]
    assert base["A안"]["total"] == 3_840_000 and base["B안"]["total"] == 9_720_000
    assert (base["B안"]["first_year"], base["B안"]["next_year"]) == (9_720_000, 6_720_000)
    assert (base["A안"]["first_year"], base["A안"]["next_year"]) == (3_840_000, 3_840_000)
    discounted = next(sc for sc in costs["scenarios"]
                      if sc["name"] == "연 단위 계약 시")["options"]
    assert discounted["B안"]["total"] == 8_712_000
    assert (discounted["B안"]["first_year"], discounted["B안"]["next_year"]) == (
        8_712_000, 5_712_000)
    block = report_checks._cost_block(costs)
    for figure in ("8,712,000원", "5,712,000원", "6,720,000원", "3,840,000원"):
        assert figure in block


def test_a_discount_from_a_later_year_counts_only_those_years():
    text = "3년 비교. 월 10,000원 × 10명, 2년 차부터 10% 할인. 다른 안은 연 1,000,000원."
    numbers = report_checks._material_numbers(text) + report_checks._calendar(text)
    reply = {"years": 3, "options": [
        {"name": "가", "items": [
            {"name": "구독", "unit_price": 10000, "count": 10, "per": "month"},
            {"name": "2년 차 할인", "unit_price": 10000, "count": 10, "per": "month", "rate": 0.1,
             "sign": -1, "from_year": 2}]},
        {"name": "나", "items": [{"name": "연 요금", "unit_price": 1000000, "count": 1,
                                  "per": "year"}]}]}
    costs = report_checks.option_costs(reply, numbers)
    가 = costs["options"]["가"]
    # 3 years × 1,200,000 − 2 years × 120,000.
    assert 가["total"] == 3_600_000 - 240_000
    assert (가["first_year"], 가["next_year"]) == (1_200_000, 1_080_000)
    assert costs["options"]["나"]["total"] == 3_000_000


def test_the_cost_block_s_figures_ground_the_writer_s_amounts():
    trace = __import__("app.services.skill_scripts", fromlist=["entry"]).entry("grounding-check")
    numbers = report_checks._material_numbers(COLLAB) + report_checks._calendar(COLLAB)
    block = report_checks._cost_block(report_checks.option_costs(COLLAB_REPLY, numbers))
    text = ("B안은 연 단위 계약 시 첫해 8,712,000원, 이듬해부터 연 5,712,000원이다. "
            "A안은 연 3,840,000원으로, 첫해 차이는 4,872,000원이다.")
    assert trace.check(text, COLLAB, [block]) == []
