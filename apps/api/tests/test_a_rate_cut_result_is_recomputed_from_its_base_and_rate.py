"""A base, a rate and the result they come to: recomputed when the material gives both,
marked otherwise."""

from __future__ import annotations

import time

import pytest

from app.pipeline import report_stages
from app.pipeline.bundle import Bundle, Request
from app.services import units

L27 = "local/qwen3.8-27b"

# ── the weekly report's price-cut table (3,200만원 × 95% written as 1억 2,800만원) ──

WEEKLY_REQUEST = (
    "아래 이번 주 메모로 팀장님께 올릴 주간 업무 보고서를 만들어줘. 영업2팀 박서연 대리, "
    "2026년 10월 3주차(10/12~10/16).\n\n"
    "[이번 주 메모 - 영업2팀 박서연 대리, 10/12~10/16]\n"
    "- 월: 신규 거래처 '대성유통' 1차 미팅. 월 발주 예상 3,200만원. "
    "단가 협상 잔여(5% 인하 요구).\n"
    "- 화: 기존 거래처 '한솔마트' 재고 클레임 2건 처리 완료(반품 420만원). 원인은 9월 물류센터 "
    "이전 시 출고 지연.\n"
    "- 수: 3분기 마감 자료 정리. 팀 매출 41.2억(목표 40억 대비 103%), 전분기 38.5억 대비 +7%.\n"
    "- 목: 대성유통 견적서 발송. 경쟁사 A사가 동일 품목 4% 낮은 단가 제시한 것으로 "
    "파악됨(확정 아님).\n"
    "- 요청사항: 단가 인하 폭 결재 필요(최대 4%까지 허용 여부), 물류팀 출고 SLA 협의 자리 마련.\n")
WEEKLY_SECTION = (
    "단가 인하 폭을 결정해 줄 필요가 있습니다. 경쟁사 A사가 동일 품목에 4% 낮은 단가를 "
    "제시한 것으로 "
    "파악되었으나 확정된 사실이 아니므로, 아래 표의 두 안을 비교해 결정해 주십시오(표 3).\n\n"
    "**표 3. 대성유통 단가 인하 안 비교**\n\n"
    "| 안 | 금액(월 발주 3,200만원 기준) | 조건 |\n"
    "|---|---|---|\n"
    "| 1안 (5% 인하) | 3,200만원 × (1 - 5%) = 1억 2,800만원 | 거래처 요구 충족, 내부 허용 범위 "
    "초과 여부 결재 요청 |\n"
    "| 2안 (4% 인하) | 3,200만원 × (1 - 4%) = 9,600만원 | 내부 상한 내에서 경쟁 대응 가능 |\n")


def test_a_cut_written_as_one_minus_a_rate_is_recomputed_in_the_stored_section():
    bundle = Bundle(request=Request(text=WEEKLY_REQUEST, model=L27, api_key="k"))
    section = {"heading": "이슈 및 요청 사항", "content": WEEKLY_SECTION}
    clean = report_stages._cleaned(bundle, 0, section, WEEKLY_SECTION)
    assert "3,200만원 × (1 - 5%) = 3,040만원" in clean
    assert "3,200만원 × (1 - 4%) = 3,072만원" in clean
    assert "1억 2,800만" not in clean and "9,600만" not in clean


def test_a_rate_in_a_written_formula_is_a_hundredth():
    assert units.fix_written_sums("3,200만원 × (1 - 5%) = 1억 2,800만원") == (
        "3,200만원 × (1 - 5%) = 3,040만원")
    assert units.fix_written_arithmetic("3,200만원 × 96% = 9,600만원") == (
        "3,200만원 × 96% = 3,072만원")
    # a right raise is not 「corrected」 to the rate read as a whole number
    assert units.fix_written_sums("매출 1,000만원 × (1 + 10%) = 1,100만원") == (
        "매출 1,000만원 × (1 + 10%) = 1,100만원")


def test_a_cut_in_words_is_recomputed_when_the_material_gives_base_and_rate():
    out = units.fix_rate_results(
        "대성유통 월 발주 3,200만원에서 5% 인하하면 1억 2,800만원이 됩니다.", WEEKLY_REQUEST)
    assert out == "대성유통 월 발주 3,200만원에서 5% 인하하면 3,040만원이 됩니다."
    table = ("| 안 | 금액(월 발주 3,200만원 기준) | 조건 |\n|---|---|---|\n"
             "| 1안 (5% 인하) | 1억 2,800만원 | 결재 |\n| 2안 (4% 인하) | 3,072만원 | 가능 |")
    assert units.fix_rate_results(table, WEEKLY_REQUEST) == table.replace(
        "1억 2,800만원", "3,040만원")
    after = "| 안 | 현재 | 인하 후 |\n|---|---|---|\n| 1안 (5% 인하) | 3,200만원 | 9,600만원 |"
    assert units.fix_rate_results(after, WEEKLY_REQUEST).endswith("| 3,200만원 | 3,040만원 |")


def test_a_cut_result_off_figures_the_material_does_not_give_is_marked():
    out = units.fix_rate_results(
        "월 발주 5,000만원에서 5% 인하하면 4,000만원이 됩니다.", WEEKLY_REQUEST)
    assert out == "월 발주 5,000만원에서 5% 인하하면 4,000만원(확인 필요)이 됩니다."


def test_the_change_itself_a_part_and_unrelated_amounts_stand():
    for text in ("월 발주 3,200만원에서 5% 인하 시 160만원이 줄어듭니다.",
                 "월 발주 3,200만원에서 4% 인하하면 3,072만원입니다.",
                 "반품 420만원과 5% 인하 요구, 3,200만원 발주.",
                 "| 안 | 현재 | 비고 |\n|---|---|---|\n"
                 "| 1안 (5% 인하) | 3,200만원 | 반품 420만원 |"):
        assert units.fix_rate_results(text, WEEKLY_REQUEST) == text
    part = units.fix_rate_results("월 발주 3,200만원의 5%인 300만원을 깎습니다.", WEEKLY_REQUEST)
    assert part == "월 발주 3,200만원의 5%인 160만원을 깎습니다."


def test_rate_results_finish_quickly_on_long_runs():
    for text in (" " * 50_000, "%" * 50_000, "3,200만원에서 " + " " * 50_000 + "5% 인하하면 1억원",
                 "| " + "5% 인하 " * 5_000 + "|\n|---|\n| 1 |"):
        start = time.monotonic()
        units.fix_rate_results(text, WEEKLY_REQUEST)
        assert time.monotonic() - start < 0.5


# ── a section edit: the rewritten lines are checked, the lines it kept stay ──

KEPT = "| 기존 | 3,200만원 × (1 - 5%) = 1억 2,800만원 | 이전 판 |"
EDITED_BEFORE = (
    "단가 인하 폭을 결정해 주십시오(표 3).\n\n**표 3. 대성유통 단가 인하 안 비교**\n\n"
    "| 안 | 금액 | 조건 |\n|---|---|---|\n" + KEPT + "\n")
EDITED_AFTER = (
    "단가 인하 폭을 결정해 주십시오(표 3). 월 발주 3,200만원에서 4% 인하하면 9,600만원이 "
    "됩니다.\n\n**표 3. 대성유통 단가 인하 안 비교**\n\n| 안 | 금액 | 조건 |\n|---|---|---|\n"
    + KEPT + "\n| 2안 (4% 인하) | 3,200만원 × (1 - 4%) = 9,600만원 | 내부 상한 내 |\n")


def test_an_edit_s_rewritten_lines_are_recomputed_and_its_kept_lines_left():
    from app.services import revise
    fixed = revise.on_new_lines(
        EDITED_BEFORE, EDITED_AFTER,
        lambda t: units.fix_rate_results(units.fix_written_sums(t), WEEKLY_REQUEST))
    assert "4% 인하하면 3,072만원이 됩니다" in fixed
    assert "= 3,072만원 | 내부 상한 내 |" in fixed
    assert KEPT in fixed


@pytest.mark.asyncio
async def test_the_section_edit_checks_the_arithmetic_it_wrote(monkeypatch):
    from app.pipeline import revise_stages
    from app.pipeline.stage import Context
    from app.services import revise

    async def rewrite_section(**kwargs):
        return EDITED_AFTER, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    parts = [{"id": "s0", "heading": "이슈 및 요청 사항", "content": EDITED_BEFORE,
              "format": "markdown"}]
    bundle = revise_stages.bundle_for(
        request=WEEKLY_REQUEST, instruction="1절에 2안을 더해줘", model={"id": L27},
        planner=None, api_key="k", is_deck=False, title="주간 보고", data={}, parts=parts,
        material=[], plan=revise.Plan(scope="parts", targets=[0], note="1절에 2안을 더해줘",
                                      clauses={0: ["1절에 2안을 더해줘"]}),
        format_id="", usage={"inputTokens": 0, "outputTokens": 0})
    bundle.plan.order = [0]
    try:
        async for _ in revise_stages.Rewrite().run(bundle, Context()):
            pass
    except revise_stages.Stop:
        pass
    content = bundle.document.sections[0]["content"]
    assert "3,072만원" in content and "9,600만원" not in content
    assert KEPT in content
