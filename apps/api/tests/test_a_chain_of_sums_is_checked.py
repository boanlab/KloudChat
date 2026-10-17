"""Whole-sum checks on worked sums of several terms, × before +."""

from app.services import arithmetic


def test_a_wrong_chain_is_found_and_a_right_one_is_not():
    wrong = "※ 시상금 합계: 500 + 300 × 2 + 100 × 4 + 50 × 6 + 100 + 50 × 2 = 900(단위: 만 원)"
    found = arithmetic.findings(wrong)
    assert len(found) == 1 and "2,000" in found[0]["message"]
    assert arithmetic.findings("500 + 300 × 2 + 100 × 4 + 50 × 6 + 100 + 50 × 2 = 2,000") == []
    # Two terms, as before.
    assert arithmetic.findings("62만 원 × 36개월 = 2,232만 원") == []
    assert len(arithmetic.findings("62만 원 × 36개월 = 2,000만 원")) == 1
    assert arithmetic.findings("2,400 + 1,140 = 3,540만 원") == []
