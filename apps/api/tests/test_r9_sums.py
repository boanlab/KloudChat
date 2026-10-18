"""Wrong totals, chained results and conclusions corrected or marked by code."""

from __future__ import annotations

import asyncio

import pytest

from app.pipeline import chat_stages, report_stages
from app.pipeline.bundle import Bundle, Computed, Document, Material, Request
from app.pipeline.stage import Context
from app.services import units

L27, LFL = "local/qwen3.8-27b", "local/qwen3.8-flash"
STRONG_EXT = "deepseek/deepseek-v4-pro-0813"
WRITERS = pytest.mark.parametrize("writer", [L27, LFL, STRONG_EXT])


def _normalized(answer: str, request: str, *, writer: str,
                tool_outputs: list[str] | None = None) -> str:
    bundle = chat_stages.bundle_for(
        answer=answer, request=request, model={"id": writer}, actual_model=None, api_key="k",
        session_id="s", today=None, first_message=request,
        messages=[{"role": "user", "content": request}], material="", check_keys=(),
        check_material="", tool_outputs=tool_outputs or [], judge="", kept_steps=[], usage={},
        evidence=[])

    async def go():
        return [e async for e in chat_stages.Normalize().run(bundle, Context())]

    asyncio.run(go())
    return bundle.document.answer


# ── 329,600원 in the head sentence and the total row (429,600원) ─────────

A05_REQUEST = ("다음 주 부산 출장 2박 3일이야. 직급은 대리(6급 상당)이고, KTX 왕복 119,600원, "
               "숙소는 1박 95,000원짜리로 잡았어. 여비 총 얼마 받을 수 있어?")
A05_ANSWER = (
    "부산 출장 2박 3일 여비는 운임 119,600원 + 일비 75,000원 + 식비 75,000원 + 숙박비 160,000원"
    "으로 합계 329,600원입니다. [별표 1] 부산 상한은 1박 80,000원이라 1박 95,000원 중 15,000원을 "
    "초과해 이 부분은 본인이 부담합니다.\n\n"
    "| 항목 | 근거 조항 | 식 | 금액 |\n"
    "|---|---|---|---:|\n"
    "| 운임(KTX 왕복) | 제6조① | 실비 119,600원 (일반실 상한 내) | 119,600원 |\n"
    "| 일비 | 제7조 | 25,000원 × 3일 | 75,000원 |\n"
    "| 식비 | 제8조 | 25,000원 × 3일 | 75,000원 |\n"
    "| 숙박비 | 제9조①·[별표 1] | min(95,000원, 80,000원) × 2박 | 160,000원 |\n"
    "| **합계** | | 119,600 + 75,000 + 75,000 + 160,000 | **329,600원** |\n\n"
    "처리 방법:\n"
    "- 사유서로 소명하면 1박당 24,000원까지 추가 지급되어 최대 1박 104,000원, 2박 208,000원"
    "(총 377,600원)까지 받을 수 있습니다.")


@WRITERS
def test_a_sum_after_its_listed_amounts_and_the_total_row_are_recomputed(writer):
    out = _normalized(A05_ANSWER, A05_REQUEST, writer=writer)
    assert "숙박비 160,000원으로 합계 429,600원입니다." in out
    assert "| **합계** | | 119,600 + 75,000 + 75,000 + 160,000 | **429,600원** |" in out
    assert "329,600" not in out
    # a conditional total is no restatement of the corrected one
    assert "(총 377,600원)까지" in out


def test_a_listed_sum_the_code_cannot_read_whole_is_left():
    # a product in the list: its tail is no sum
    text = "일비 25,000원 × 3일 + 식비 75,000원 + 숙박비 160,000원으로 합계 300,000원입니다."
    assert units.fix_written_sums(text) == text
    # a unit price beside totals: 1000× apart, no addition
    text = "단가 1,200원 + 총 매출 3,000,000원으로 합계 3,100,000원입니다."
    assert units.fix_written_sums(text) == text
    right = "운임 119,600원 + 일비 75,000원으로 합계 194,600원입니다."
    assert units.fix_written_sums(right) == right


# ── a bold result after 「=」, the check script having printed it ────────

LFLF_SENTENCE = ("이때 숙박비는 190,000원, 여비 합계는 119,600 + 75,000 + 75,000 + 190,000 = "
                 "**477,600원**입니다.")
CALC_CHECK_STDOUT = ("[일치] 특례 적용 합계: 119600+75000+208000+75000 = 477,600 "
                     "(적힌 값 477,600)")


def test_a_bold_result_is_recomputed():
    assert units.fix_written_sums(LFLF_SENTENCE) == LFLF_SENTENCE.replace("477,600", "459,600")


@WRITERS
def test_a_result_a_tool_printed_is_marked_never_rewritten(writer):
    out = _normalized(LFLF_SENTENCE, A05_REQUEST, writer=writer, tool_outputs=[CALC_CHECK_STDOUT])
    assert "= **477,600원**(확인 필요)입니다." in out


# ── table totals: subtotals, second totals, rates and units of their own ──────────────

def test_a_total_over_subtotals_or_a_second_total_is_read_both_ways_and_only_marked():
    table = ("| 항목 | 금액 |\n|---|---|\n| 인건비 | 300만 원 |\n| 장비 | 200만 원 |\n"
             "| 소계 | 500만 원 |\n| 부가세 | 50만 원 |\n| 합계 | 550만 원 |")
    assert units.fix_table_totals(table) == table
    wrong = table.replace("| 합계 | 550만 원 |", "| 합계 | 650만 원 |")
    assert units.fix_table_totals(wrong) == wrong.replace("650만 원", "650만 원(확인 필요)")
    two = ("| 항목 | 금액 |\n|---|---|\n| 공급가 | 1,000,000원 |\n| 배송 | 50,000원 |\n"
           "| 합계 | 1,050,000원 |\n| 부가세 | 105,000원 |\n| 총계 | 1,155,000원 |")
    assert units.fix_table_totals(two) == two


def test_rates_averages_and_mixed_units_are_not_added():
    rates = ("| 구분 | 인원 | 비율 |\n|---|---|---|\n| A | 47곳 | 42.0% |\n| B | 38곳 | 33.9% |\n"
             "| C | 27곳 | 24.1% |\n| 합계 | 112곳 | 100% |")
    assert units.fix_table_totals(rates) == rates
    average = ("| 월 | 매출 |\n|---|---|\n| 1월 | 300만 원 |\n| 2월 | 500만 원 |\n"
               "| 평균 | 400만 원 |\n| 합계 | 800만 원 |")
    assert units.fix_table_totals(average) == average
    mixed = ("| 항목 | 값 |\n|---|---|\n| 직원 | 6명 |\n| 월세 | 600만 원 |\n"
             "| 합계 | 900만 원 |")
    assert units.fix_table_totals(mixed) == mixed
    plain = ("| 항목 | 금액 |\n|---|---|\n| 운임 | 119,600원 |\n| 숙박 | 160,000원 |\n"
             "| 합계 | 279,600원 |")
    assert units.fix_table_totals(plain) == plain


# ── a chain ten times over; restatements; a company label on invented shares ─

A10_CHAIN = (
    "상향식 TAM은 학생 수 500만 명[2] × 연 비용 118,800원 = 5조 9,400억 원, SAM은 5조 9,400억 원 "
    "× 30% = 1조 7,820억 원, SOM은 1조 7,820억 원 × 2% = 356억 4,000만 원으로 계산합니다.\n\n"
    "| 구분 | 계산식 | 산출 값 |\n|---|---|---|\n"
    "| TAM | 학생 수 500만 명 × 연 구독료 118,800원 | 5조 9,400억 원 |\n"
    "| SAM | TAM × 침투율 30% | 1조 7,820억 원 |")


def test_a_wrong_step_of_a_chain_is_carried_into_the_next_steps():
    out = units.fix_written_arithmetic(A10_CHAIN)
    assert ("500만 명[2] × 연 비용 118,800원 = 5,940억 원, SAM은 5,940억 원 × 30% = 1,782억 원, "
            "SOM은 1,782억 원 × 2% = 35억 6,400만 원") in out
    assert "| TAM | 학생 수 500만 명 × 연 구독료 118,800원 | 5,940억 원 |" in out
    assert "조" not in out
    # a right chain stays
    right = "SAM은 29조 2천억 원 × 30% = 8조 7,600억 원, SOM은 8조 7,600억 원 × 2% = 1,752억 원"
    assert units.fix_written_arithmetic(right) == right


def test_a_figure_ten_times_a_worked_result_under_its_name_is_marked():
    body = "SOM은 8조 7,600억 원 × 2% = 1,752억 원으로 계산합니다."
    summary = "하향식 SOM은 175억 2천만 원, 상향식 SOM은 35억 6,400만 원으로 산출되었습니다."
    assert units.mark_scaled_restatements(summary, body) == summary.replace(
        "175억 2천만 원", "175억 2천만 원(확인 필요)")
    table = "| SOM (점유율 2%) | 1조 7,520억 원 | 35억 6,400만 원 |"
    assert "1조 7,520억 원(확인 필요)" in units.mark_scaled_restatements(table, body)
    # another quantity ten times over is its own figure
    fee = "연 구독료 9,900원 × 12 = 118,800원이고, 10년 누적 지출은 1,188,000원입니다."
    assert units.mark_scaled_restatements(fee) == fee


def test_a_company_label_on_figures_the_person_never_gave_becomes_an_assumption():
    request = ("우리 가정(한빛소프트 사내 가정, 가상): 월 구독료 9,900원, 초기 3년 목표 점유율은 "
               "유료 이용 학생 기준 2%")
    table = ("| 항목 | 값 | 근거 |\n|---|---|---|\n"
             "| 월 구독료 | 9,900원 | 가정(사내) |\n"
             "| 연 구독료 | 118,800원 (9,900 × 12) | 가정(사내) |\n"
             "| 목표 점유율(초기 3년) | 2% (유료 이용 학생 기준) | 가정(사내) |\n"
             "| 고객군 비중 | 학부모 70% / 학원 25% / 학교·교육청 5% | 가정(사내) |")
    out = units.label_assumptions(table, request)
    assert "| 고객군 비중 | 학부모 70% / 학원 25% / 학교·교육청 5% | 가정(본 검토 설정) |" in out
    assert out.count("가정(사내)") == 3


def _report_bundle(content: str, status: str) -> Bundle:
    return Bundle(
        request=Request(text="월 구독료 9,900원, 목표 점유율 2%", model="w", api_key="k"),
        material=Material(given=""),
        computed=Computed(values="9,900원 × 12 = 118,800원", calc_status=status),
        document=Document(sections=[{"heading": "산출", "content": content}]))


@pytest.mark.parametrize("status", ["timeout", "partial"])
def test_after_an_unfinished_calculation_the_writer_s_results_read_as_estimates(status):
    content = ("연 비용은 9,900원 × 12 = 118,800원입니다. 학생 483만 명 × 118,800원 = 5,738억 원, "
               "5,738억 원 × 2% = 114억 7,600만 원(추정)입니다.")
    bundle = _report_bundle(content, status)
    out = report_stages._figures_marked(bundle, bundle.document.sections)[0]["content"]
    # code's own figure stays bare, the writer's gets 「(추정)」, a marked one once
    assert "= 118,800원입니다." in out
    assert "= 5,738억 원(추정)," in out
    assert out.count("(추정)") == 2


def test_after_a_finished_calculation_nothing_is_labelled():
    content = "학생 483만 명 × 118,800원 = 5,738억 원입니다."
    bundle = _report_bundle(content, "done")
    assert report_stages._figures_marked(bundle, bundle.document.sections)[0]["content"] == content


# ── a product a unit off ─────────────────────────────────────────────────

@WRITERS
def test_a_formula_a_unit_off_is_marked(writer):
    answer = "1차년 계약 25곳 × 1명 × 40만 원/석 × 12개월 = 1200억 원(연간)."
    out = _normalized(answer, "협업툴 시장 진입 검토", writer=writer)
    assert "= 1200억 원(확인 필요)(연간)" in out


# ── an operating profit its own figures do not leave ────────────────────────

P56_REQUEST = ("직원 6명짜리 동네 베이커리 카페야. 연 매출은 약 4억 원, 임대료가 월 600만 원, "
               "원재료비는 매출의 35%쯤 돼. 2년 안에 2호점을 내고 싶어.")
P56_HEAD = (
    "주어진 숫자만으로는 2호점 자금이 2년 안에 모이는지 단정할 수 없습니다. 연매출 4억 원에서 "
    "원재료비 1억 4,000만 원(35%)과 임대료 7,200만 원을 뺀 나머지가 인건비 6명과 공공요금, 기타 "
    "운영비로 얼마나 남느냐가 전부이기 때문입니다. 인건비를 월 1,800만 원(직원 1인 평균 300만 원, "
    "추정)으로 잡으면 세전 영업이익은 연 1억 원 안팎이고, 여기서 세금과 대표 인출을 빼면 2년 누적 "
    "가용현금은 1억 원대 초반입니다.")


@WRITERS
def test_a_profit_its_own_figures_do_not_leave_is_marked_not_rewritten(writer):
    out = _normalized(P56_HEAD, P56_REQUEST, writer=writer)
    assert ("세전 영업이익은 연 1억 원(확인 필요: 4억 − 1억 4,000만 − 7,200만 − 2억 1,600만 = "
            "−2,800만 원) 안팎") in out
    assert out.replace("(확인 필요: 4억 − 1억 4,000만 − 7,200만 − 2억 1,600만 = −2,800만 원)",
                       "") == P56_HEAD
    assert units.mark_profit_contradictions(out) == out


def test_a_profit_its_figures_allow_or_a_paragraph_without_one_revenue_is_left():
    fits = ("연매출 4억 원에서 원재료비 1억 4,000만 원과 임대료 7,200만 원, 인건비 월 1,000만 원을 "
            "빼면 영업이익은 연 6,800만 원입니다.")
    assert units.mark_profit_contradictions(fits) == fits
    # unnamed costs only lower the profit: a lower one is no contradiction
    lower = fits.replace("6,800만 원", "4,000만 원")
    assert units.mark_profit_contradictions(lower) == lower
    # 1호점 example with no revenue in the paragraph
    example = ("1호점 월 영업이익이 800만 원인 상태에서 2호점 월 고정비가 1,000만 원이면 매달 "
               "200만 원씩 소진합니다.")
    assert units.mark_profit_contradictions(example) == example
    monthly = ("월 매출 3,000만 원에서 임대료 600만 원과 인건비 1,800만 원을 빼면 영업이익은 "
               "월 600만 원입니다.")
    assert units.mark_profit_contradictions(monthly) == monthly
