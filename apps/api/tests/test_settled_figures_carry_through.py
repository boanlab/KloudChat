"""The figures the material settled (판매가, 변동비, 고정비 …) are carried as fixed values:
the writer is told them, and what it wrote is read against them."""

from __future__ import annotations

import asyncio

from app.services import key_figures as kf

MATERIAL = (
    "박스당 판매가는 20,000원, 변동비는 8,000원이라 박스당 마진은 12,000원입니다. "
    "월 고정비 3,000만 원을 마진으로 나누면 손익분기는 2,500박스입니다."
)
FIGURES = [
    kf.Figure("박스당 판매가", "20,000원", ["판매가", "객단가"]),
    kf.Figure("박스당 변동비", "8,000원", ["변동비"]),
    kf.Figure("월 고정비", "3,000만 원", ["고정비"]),
    kf.Figure("박스당 마진", "12,000원", ["마진", "공헌이익"]),
]


def test_a_figure_is_kept_only_when_the_material_gives_it_and_does_not_contest_it():
    reply = """[
      {"name": "박스당 판매가", "value": "20,000원", "aliases": ["판매가"]},
      {"name": "월 고정비", "value": "3,500만 원", "aliases": ["고정비"]},
      {"name": "목표 판매량", "value": "4,000박스"}
    ]"""
    kept = kf.parse(reply, MATERIAL)
    # 3,500만 원 and 4,000박스 are the model's, not the material's.
    assert [f.name for f in kept] == ["박스당 판매가"]
    contested = MATERIAL + " 다른 표에서는 판매가는 18,000원으로 적었다."
    assert kf.parse(reply, contested) == []


def test_a_settled_figure_written_with_another_value_is_found():
    wrong = [
        "박스 판매가는 200만원, 박스당 변동비는 8,000원으로 잡습니다.",
        "1박스당 판매가 20,000원, 변동비 8,000원, 박스당 마진 1,200만원입니다.",
        "공헌이익 12,000원 × 월 4,000박스 - 월 고정비 300만원 = 월 영업이익입니다.",
    ]
    for sentence in wrong:
        assert kf.conflicts(sentence, FIGURES), sentence


def test_a_formula_operand_a_total_or_another_case_is_not_a_clash():
    fine = [
        "공헌이익은 20,000원 - 8,000원 = 12,000원으로 고정됩니다.",
        "총마진 48,000,000원 (4,000박스 × 12,000원)을 통해 고정비를 덮습니다.",
        "초기 재고 2,000박스분 변동비 1,600만 원을 더하지 않으면 현금이 먼저 나갑니다.",
        "변동비가 박스당 12,000원으로 나빠지면 공헌이익이 8,000원으로 줄어듭니다.",
        "원가 절감이 실현되면 변동비는 7,440원으로 낮아집니다.",
        "| 냉장 배송 단가 상승 | 공헌이익 8,000원/박스로 축소 |",
    ]
    for sentence in fine:
        assert kf.conflicts(sentence, FIGURES) == [], sentence


def test_a_row_named_for_a_figure_takes_its_value():
    table = "| 항목 | 값 |\n| --- | --- |\n| 판매가 | 2,000원 |\n| 고정비 | 3,000만 원 |"
    mended = kf.mend_rows(table, FIGURES)
    assert "| 판매가 | 20,000원 |" in mended and "| 고정비 | 3,000만 원 |" in mended


def test_settling_asks_once_and_skips_material_without_amounts():
    calls = []

    async def complete(model, messages, api_key, max_tokens):
        calls.append(messages)
        return '[{"name": "월 고정비", "value": "3,000만 원", "aliases": ["고정비"]}]', {
            "inputTokens": 3, "outputTokens": 2}

    figures, spent = asyncio.run(kf.settle(MATERIAL, complete, "m", "k"))
    assert [f.value for f in figures] == ["3,000만 원"] and spent["inputTokens"] == 3
    assert asyncio.run(kf.settle("원소와 화합물 수업", complete, "m", "k"))[0] == []
    assert len(calls) == 1
    assert "3,000만 원" in kf.block(figures) and "확정 수치" in kf.block(figures)


def test_a_settled_price_slipped_inside_a_formula_is_set_back_and_the_sum_redone():
    """A slipped price inside a formula is set back and the sum redone: 「6,000만 원(3,000박스 ×
    2,000원)」 with the price settled at 20,000원, and
    「(4,000박스 × 12,000원) - 3,000만 원 = 영업이익 1억 8,000만 원」."""
    from app.services import units

    slipped = "6개월 차 월 매출은 3,000박스 × 판매가 2,000원 = 600만 원입니다."
    assert kf.mend_operands(slipped, FIGURES) == (
        "6개월 차 월 매출은 3,000박스 × 판매가 20,000원 = 600만 원입니다.")
    # Another quantity with the same digits is left: a 12만 원 subscription is not the
    # 12,000원 margin slipped.
    fee = "SOM은 850 가구 × 월 구독료 12만 원 × 12 개월입니다."
    assert kf.mend_operands(fee, FIGURES) == fee
    # The mended operand stays: the agreement pass leaves a formula's operands alone.
    mended = kf.mend_operands("월 매출은 1,000명 × 판매가 20,000,000원 = 20,000,000원", FIGURES)
    expected = "월 매출은 1,000명 × 판매가 20,000원 = 20,000,000원"
    assert units.fix_written_arithmetic(mended) == expected
    profit = "목표 4,000박스면 (4,000박스 × 12,000원) - 3,000만 원 = 영업이익 1억 8,000만 원입니다."
    assert "= 영업이익 1,800만 원" in units.fix_written_sums(profit)
    loss = "3월 적자는 (500 × 12,000원) - 1,500만 원 = 월 9,000만 원으로 줄어듭니다."
    assert "= 월 900만 원" in units.fix_written_sums(loss)
    right = "영업이익은 4,000×12,000원−3,000만 원=1,800만 원입니다."
    assert units.fix_written_sums(right) == right


def test_a_sum_missing_a_quantity_is_left_and_a_far_slipped_operand_is_mended():
    from app.services import units

    broken = "이달 현금 소진액은 3,000만 원 - 12,000 원 = 1,800만 원입니다."
    assert units.fix_written_sums(broken) == broken
    far = "100박스당 영업이익 변동은 100박스 × 마진 1억 2,000만원 = 1억 2,000만 원입니다."
    mended = units.fix_written_arithmetic(kf.mend_operands(far, FIGURES))
    assert mended == "100박스당 영업이익 변동은 100박스 × 마진 12,000원 = 120만 원입니다."


def test_the_judge_reads_only_the_figure_sentences_the_code_let_through(monkeypatch):
    from app.services import report

    sections = [{"heading": "수익", "content": (
        "월 고정비 300만원으로 계산합니다. "            # the code flags this one itself
        "판매가를 그대로 두고 월 4,000박스를 팔면 이익이 납니다. "
        "4,000박스 기준 고정비 3,000 원을 씁니다."      # the code passes it; the judge does not
    )}]
    asked = []

    async def complete(model, messages, api_key, max_tokens):
        sentence = messages[0]["content"].split("문장: ", 1)[1].split("\n", 1)[0]
        asked.append(sentence)
        return ("예" if "3,000 원" in sentence else "아니오"), {"inputTokens": 2, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    found, spent = asyncio.run(report.judge_figure_sentences(
        sections, FIGURES, judge="local/qwen3.5-122b", api_key="k"))
    assert not any("300만원" in s for s in asked)
    assert [f[1] for f in found] == ["4,000박스 기준 고정비 3,000 원을 씁니다."]
    assert spent["inputTokens"] == 2 * len(asked)


def test_what_the_inputs_give_is_worked_out_by_code_and_checked_like_them():
    """Combined values (「목표 이익 1억 8,000만」, 「손익분기 1,500박스」) are worked out by code
    from the settled inputs and checked like them."""
    inputs = [
        kf.Figure("박스당 판매가", "20,000원", ["판매가"]),
        kf.Figure("박스당 변동비", "8,000원", ["변동비"]),
        kf.Figure("월 고정비", "3,000만 원", ["고정비"]),
        kf.Figure("목표 판매량", "4,000박스", []),
    ]
    made = {f.name: f.value for f in kf.derived(inputs)}
    assert made == {
        "박스당 공헌이익": "12,000원", "손익분기 판매량": "2,500박스", "손익분기 매출": "5,000만원",
        "목표 판매량 시 월 영업이익": "1,800만원", "목표 판매량 시 월 매출": "8,000만원",
    }
    settled = [*inputs, *kf.derived(inputs)]
    assert kf.conflicts("목표 영업이익 1억 8,000만원을 달성합니다.", settled)
    assert kf.conflicts("손익분기 판매량은 1,500박스입니다.", settled)
    # A material whose own margin disagrees with price minus cost: nothing is derived.
    odd = [*inputs, kf.Figure("박스당 마진", "10,000원", ["마진"])]
    assert kf.derived(odd) == []
