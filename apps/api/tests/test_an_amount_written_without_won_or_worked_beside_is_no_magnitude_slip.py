"""An amount the request states without 「원」, or one whose own product stands beside it, is no
power-of-ten slip of a ledger result.

Round-3 r3r04 (11-21, both arms): the request gave 「고정비: 월 4,500만 원(인건비 3,600만 +
클라우드·사무실 900만)」 and 「구독 운영 원가 대당 월 3만 원」. The ledger read 3만 원 × 12개월 =
36만 원; 3,600만 is a hundred times that, and 「3,600만」 was not counted as stated because no 「원」
followed it, so every 「인건비 3,600만 원」 became 「인건비 36만 원」, and 「3,600만 원(300대 × 12만
원)」 became 「36만 원(300대 × 12만 원)」."""

from __future__ import annotations

from app.services import report

ASK = ("세탁 장비 렌털 사업계획서를 써 줘.\n"
       "- 렌털료: 장비 1대당 월 18만 원, 점검 원가 대당 월 3만 원\n"
       "- 고정비: 월 5,000만 원(인력 3,600만 + 창고 임차 1,400만)\n"
       "- 1년 사업 기간")


def test_a_stated_amount_without_won_stays_as_written() -> None:
    text = "고정비는 월 5,000만 원(인력 3,600만 원 + 창고 임차 1,400만 원)입니다.\n"
    assert report.fix_ledger_magnitudes(text, ASK) == text


def test_an_amount_with_its_own_product_beside_it_stays() -> None:
    text = ("렌털 이익은 월 360만 원(20대 × 18만 원)입니다.\n"
            "200대 × 18만 원 = 3,600만 원이 월 렌털료입니다.\n")
    assert report.fix_ledger_magnitudes(text, ASK) == text


def test_a_ledger_result_written_a_digit_off_is_still_restored() -> None:
    assert "36만 원" in report.derived_values(ASK)[1]
    assert report.fix_ledger_magnitudes("연간 점검 원가는 대당 360만 원입니다.\n", ASK) == (
        "연간 점검 원가는 대당 36만 원입니다.\n")
