"""A figure the answer works out itself is its own value, never a restatement slipped by a digit."""

from __future__ import annotations

from app.services import units

ANSWER = (
    "1) 공급가액 15,000,000원, 부가세 1,500,000원이고 2) 할인 후에는 공급가액 13,500,000원, "
    "부가세 1,350,000원, 합계 14,850,000원입니다.\n\n"
    "- 공급가액 = 16,500,000 ÷ 1.1 = 15,000,000원\n"
    "- 부가세 = 16,500,000 − 15,000,000 = 1,500,000원 (공급가액의 10%)\n"
    "- 할인 후 부가세 = 13,500,000 × 0.1 = 1,350,000원\n"
)


def test_vat_and_its_base_both_stand_as_written() -> None:
    assert units.fix_written_arithmetic(ANSWER) == ANSWER


def test_a_wrong_product_is_still_corrected() -> None:
    text = "- 할인 후 부가세 = 13,500,000 × 0.1 = 1,450,000원\n"
    assert "1,350,000원" in units.fix_written_arithmetic(text)
