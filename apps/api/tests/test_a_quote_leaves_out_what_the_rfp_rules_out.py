"""quote-calc leaves out the item a requirement rules out (lineup v1 client-proposal-5__r1).

The company pack priced 외부 LLM API 사용료 next to the domestic GPU it is replaced by,
while SER-002 keeps processing in the country; the run settled 296,000,000 / 325,600,000.
"""

from __future__ import annotations

import json

from app.services import report_checks, skill_scripts

MATERIAL = (
    "# 제안요청서 — 가온구 통합도서관 AI 도서 추천·상담 챗봇 구축\n"
    "- 사업 기간: 계약일 ~ 2027. 6. 30.(계약 예정일 2026. 12. 1.)\n"
    "- 기초금액: 금330,000,000원(부가가치세 포함)\n"
    "### 보안(SER)\n"
    "- SER-001 개인정보 영향평가 대응 자료 제출\n"
    "- SER-002 회원 개인정보와 대화 기록은 국외로 이전하지 않으며, 처리 시스템은 국내에 둔다\n"
    "- SER-003 대화 기록 보관 6개월 후 파기, 파기 기록 보관\n"
    "# (주)누리데이터 회사 자료(내부, 제안서 작성용)\n"
    "## 기본 아키텍처(현행 상품 구성)\n"
    "- 답변 생성: 외부 LLM API(해외 리전) 호출, 월 사용료 약 900,000원\n"
    "- 대안: 국내 GPU 서버 임차(월 1,800,000원)로 오픈 모델 구동 가능\n"
    "## 투입 계획(가안, 7개월)\n"
    "| 역할 | 등급 | MM | 월 단가(VAT 별도) |\n|---|---|---|---|\n"
    "| PM | 특급 | 7 | 10,500,000원 |\n"
    "| AI 엔지니어 2명 | 고급 | 12 | 8,800,000원 |\n"
    "| 개발자 2명 | 중급 | 8 | 6,900,000원 |\n"
    "| 개발자 1명 | 초급 | 4 | 5,200,000원 |\n"
    "- 누리봇 v2 라이선스 1식 22,000,000원\n"
    "- 외부 LLM API 사용료 7개월 × 900,000원\n"
    "- 국내 GPU 서버 임차 7개월 × 1,800,000원\n"
)

_ITEMS = [
    {"name": "PM", "mm": 7, "monthly_price": 10500000},
    {"name": "AI 엔지니어", "mm": 12, "monthly_price": 8800000},
    {"name": "개발자(중급)", "mm": 8, "monthly_price": 6900000},
    {"name": "개발자(초급)", "mm": 4, "monthly_price": 5200000},
    {"name": "누리봇 v2 라이선스", "qty": 1, "unit": "식", "unit_price": 22000000},
    {"name": "국내 GPU 서버 임차", "qty": 7, "unit": "개월", "unit_price": 1800000},
]
#: What the run's extraction gave: every pack item, the API line included.
EVERYTHING = {
    "vat_basis": "excl",
    "items": [*_ITEMS, {"name": "외부 LLM API 사용료", "qty": 7, "unit": "개월",
                        "unit_price": 900000}],
    "budget": {"amount": 330000000, "vat_basis": "incl"},
}


def test_an_item_breaking_a_requirement_is_left_out_by_code_too():
    data, dropped = report_checks.grounded_quote_input(EVERYTHING, MATERIAL)
    assert dropped == []
    api = next(i for i in data["items"] if i["name"] == "외부 LLM API 사용료")
    assert api["excluded"] and api["reason"].startswith("SER-002")
    gpu = next(i for i in data["items"] if i["name"] == "국내 GPU 서버 임차")
    assert "excluded" not in gpu
    result = skill_scripts.quotes().run(data)
    assert (result["supply"], result["total"]) == (289_700_000, 318_670_000)


def test_the_model_s_own_exclusion_and_reason_are_kept():
    marked = {**EVERYTHING, "items": [
        *_ITEMS,
        {"name": "외부 LLM API 사용료", "qty": 7, "unit": "개월", "unit_price": 900000,
         "excluded": True, "reason": "SER-002 국외 이전 금지 — 국내 GPU 서버로 대체"},
    ]}
    data, _ = report_checks.grounded_quote_input(marked, MATERIAL)
    api = next(i for i in data["items"] if i["name"] == "외부 LLM API 사용료")
    assert api["reason"] == "SER-002 국외 이전 금지 — 국내 GPU 서버로 대체"


def test_an_unrelated_item_is_not_left_out():
    plain = "PM 1.0MM × 9,000,000원, PM 교체 불가.\nGPU 3개월 × 1,200,000원(국내 GPU로 대체).\n"
    data, _ = report_checks.grounded_quote_input({"items": [
        {"name": "PM", "mm": 1.0, "monthly_price": 9000000},
        {"name": "GPU", "qty": 3, "unit_price": 1200000},
    ]}, plain)
    assert not any(i.get("excluded") for i in data["items"])


async def test_the_block_and_the_step_say_what_was_left_out_and_why():
    async def complete(model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        assert "요구사항" in prompt and '"reason"' in prompt
        return json.dumps(EVERYTHING, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    pre = await report_checks.prepare("client_proposal", "기술 제안서 본문 초안 써 줘", MATERIAL,
                                      complete=complete, model="m", api_key="k")
    assert pre.quote["supply"] == 289_700_000 and pre.quote["total"] == 318_670_000
    block = next(b for b in pre.blocks if b.startswith("# 견적 확정 블록"))
    assert "- 제외: 외부 LLM API 사용료 (SER-002 국내 처리 요건과 충돌)" in block
    summary = next(r["summary"] for r in pre.runs if r["skill"] == "quote-calc")
    assert "합계(VAT 포함) 318,670,000원" in summary and "제외: 외부 LLM API 사용료" in summary
    figures = {f.name: f.value for f in pre.figures}
    assert figures["견적 공급가액(VAT 별도)"] == "289,700,000원"


def test_an_item_the_material_sets_aside_by_its_price_is_left_out():
    text = "- 인증 수수료 3,300,000원(발주기관 부담)\n- PM 1.0MM × 9,000,000원\n"
    data, _ = report_checks.grounded_quote_input({"items": [
        {"name": "인증 수수료", "amount": 3300000},
        {"name": "PM", "mm": 1.0, "monthly_price": 9000000},
    ]}, text)
    assert [i["name"] for i in data["items"] if i.get("excluded")] == ["인증 수수료"]


def test_the_rate_card_s_vat_basis_wins_over_the_extraction_s():
    """The run read the rate card as VAT included and settled 공급가액 269,090,909."""
    misread = {**EVERYTHING, "vat_basis": "incl"}
    assert report_checks.price_basis(MATERIAL) == "excl"
    data, _ = report_checks.grounded_quote_input(misread, MATERIAL)
    assert data["vat_basis"] == "excl"
    result = skill_scripts.quotes().run(data)
    assert result["supply"] == 289_700_000


def test_a_budget_s_basis_is_not_the_prices_basis():
    assert report_checks.price_basis("- 기초금액: 금330,000,000원(부가가치세 포함)\n"
                                     "- PM 7MM × 10,500,000원\n") == ""


# ── client-proposal-5 r1/r2: 「quote-calc: 견적 계산 실패」 ─────────────────
# The log said `quote pre-pass failed: 'unit_price'`: the extraction marked the API line
# excluded and left its price out, and the script read `item["unit_price"]`.

UNPRICED_EXCLUSION = {
    "vat_basis": "excl",
    "items": [
        {"name": "PM", "qty": 7, "unit": "MM", "monthly_price": 10500000},
        *_ITEMS[1:],
        {"name": "외부 LLM API 사용료", "qty": 7, "unit": "개월", "excluded": True,
         "reason": "SER-002 국외 이전 금지"},
    ],
}


def test_an_excluded_item_without_its_price_does_not_fail_the_quote():
    data, dropped = report_checks.grounded_quote_input(UNPRICED_EXCLUSION, MATERIAL)
    assert dropped == []
    assert [i["name"] for i in data["items"]][0] == "PM"
    assert data["items"][0]["unit_price"] == 10500000
    assert [i["name"] for i in report_checks.excluded_items(data)] == ["외부 LLM API 사용료"]
    result = skill_scripts.quotes().run(data)
    assert (result["supply"], result["vat"], result["total"]) == (
        289_700_000, 28_970_000, 318_670_000)


async def test_the_run_s_reply_settles_the_quote_and_says_what_was_left_out():
    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(UNPRICED_EXCLUSION, ensure_ascii=False), {}

    pre = await report_checks.prepare("client_proposal", "기술 제안서 본문 초안 써 줘", MATERIAL,
                                      complete=complete, model="m", api_key="k")
    run = next(r for r in pre.runs if r["skill"] == "quote-calc")
    assert not run.get("error")
    assert "합계(VAT 포함) 318,670,000원" in run["summary"]
    assert "제외: 외부 LLM API 사용료" in run["summary"]
    block = next(b for b in pre.blocks if b.startswith("# 견적 확정 블록"))
    assert "- 제외: 외부 LLM API 사용료 (SER-002 국외 이전 금지)" in block


async def test_a_failed_quote_says_why_in_the_step(monkeypatch):
    def run(options):
        raise KeyError("unit_price")

    monkeypatch.setattr(report_checks.skill_scripts.quotes(), "run", run)

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(EVERYTHING, ensure_ascii=False), {}

    pre = await report_checks.prepare("client_proposal", "제안서", MATERIAL,
                                      complete=complete, model="m", api_key="k")
    run_ = next(r for r in pre.runs if r["skill"] == "quote-calc")
    assert run_["error"] and run_["summary"] == "견적 계산 실패 (값 'unit_price' 없음)"


def test_the_script_names_the_item_that_has_no_price():
    try:
        skill_scripts.quotes().run({"items": [{"name": "라이선스", "qty": 1}]})
    except ValueError as exc:
        assert "「라이선스」" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("no price should not be computed")


# ── client-proposal-3: the 견적 메모 is in the request, the RFP is attached ────────

KAFI_REQUEST = (
    "농식품정보원 접근성 사업 제안서 초안 만들어 줘. RFP는 PDF에서 긁어서 페이지 번호가 섞여 있어. "
    "견적은 아래 메모대로 잡아 줘.\n\n"
    "[견적 메모] PM 1명 5MM(월 7,000,000원), 퍼블리셔 2명 각 4MM(월 5,500,000원), 접근성 진단 "
    "전문가 1명 1MM(월 6,000,000원), 품질인증 심사 수수료 실비 3,300,000원. 회사는 (주)누리데이터, "
    "접근성 사업 실적은 2025 도립도서관 홈페이지 접근성 개선(4개월, 6,800만 원) 1건."
)
KAFI_RFP = (
    "## rfp_kafi_accessibility.txt\n- 3 -\n제안요청서  |  한국농식품정보원  |  2026\n"
    "2. 사업기간 : 계약일로부터 5개월\n3. 소요예산 : 금 99,000,000원(부가가치세 포함)\n"
    "ACC-05 웹 접근성 품질인증 취득 지원(심사 신청·보완 대응)\n"
    " ※ 품질인증 심사 수수료는 발주기관이 별도 부담함(ACC-05 관련)\n"
)
KAFI_REPLY = {"vat_basis": "", "items": [
    {"name": "PM", "mm": 5, "monthly_price": 7000000},
    {"name": "퍼블리셔", "mm": 4, "people": 2, "monthly_price": 5500000},
    {"name": "접근성 진단 전문가", "mm": 1, "monthly_price": 6000000},
    {"name": "품질인증 심사 수수료", "amount": 3300000},
], "budget": {"amount": 99000000, "vat_basis": "incl"}}


def test_the_request_s_memo_is_material_beside_the_attachment():
    from app.services import report

    given = report._request_material(KAFI_REQUEST)
    assert "PM 1명 5MM(월 7,000,000원)" in given


def test_price_lines_in_a_one_paragraph_request_are_material():
    from app.services import report

    request = ("제안서 초안 써 줘. 견적은 PM 1명 5MM(월 7,000,000원), 퍼블리셔 2명 각 4MM(월 "
               "5,500,000원)로 잡아 줘.\n회사 소개는 첨부 자료대로.")
    assert report._request_material(request) == (
        "# 요청에 적은 자료\n제안서 초안 써 줘. 견적은 PM 1명 5MM(월 7,000,000원), 퍼블리셔 2명 각 "
        "4MM(월 5,500,000원)로 잡아 줘.")
    assert report._request_material("제안서 초안 써 줘. 회사 소개는 첨부 자료대로.") == ""


def test_the_fee_the_client_pays_is_left_out_by_the_rfp_s_own_words():
    from app.services import report

    material = report._request_material(KAFI_REQUEST) + "\n\n" + KAFI_RFP
    data, dropped = report_checks.grounded_quote_input(KAFI_REPLY, material)
    assert dropped == []
    fee = next(i for i in data["items"] if i["name"] == "품질인증 심사 수수료")
    assert fee["excluded"] and "발주기관이 별도 부담" in fee["reason"]
    result = skill_scripts.quotes().run(data)
    assert (result["supply"], result["vat"], result["total"]) == (85_000_000, 8_500_000, 93_500_000)


async def test_the_memo_in_the_request_reaches_the_quote_beside_the_attached_rfp(monkeypatch):
    from app.services import report

    seen = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = messages[0]["content"]
        if "견적 계산에 쓸 값" in prompt:
            seen.append(prompt)
            return json.dumps(KAFI_REPLY, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        return "## 견적\n견적은 아래와 같다.", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request=KAFI_REQUEST, model="m", api_key="k", web_search=False,
        untrusted_context=[KAFI_RFP], template_id="doc-client-proposal",
        approved_plan={"title": "접근성 개선 제안서", "sections": ["견적"]},
    )]
    assert seen and "PM 1명 5MM(월 7,000,000원)" in seen[0] and "발주기관이 별도 부담" in seen[0]
    step = next(e for e in events if e["type"] == "step" and e["id"] == "prepass")
    assert ("quote-calc: 견적 3항목 → 공급가액(VAT 별도) 85,000,000원, 합계(VAT 포함) "
            "93,500,000원") in step["detail"]
