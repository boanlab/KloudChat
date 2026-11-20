"""An amount the request states is the operand, never a power-of-ten slip of a ledger result.

Round-1 r1r05 (11-20, both arms): the request gave 「월 15만 원 × 최대 10개월」; the ledger
result 150만 is ten times 15만, so the slip fix turned every 「월 15만 원」 into 「월 150만 원」
(and 20만 → 200만, 10만 → 100만), and the report said the city pays 150만 원 a month."""

from __future__ import annotations

from app.services import report

ASK = ("아래 세 지자체의 청년 월세 지원 제도를 비교해 줘.\n"
       "- 누리시: 월 15만 원 × 최대 10개월, 선정 900명(예산 13억 5천만 원)\n"
       "- 가람시: 월 20만 원 × 최대 12개월\n"
       "- 한솔군: 월 10만 원 × 최대 24개월")


def test_the_stated_monthly_amounts_stay_as_written() -> None:
    text = ("| 지원액 | 월 15만 원 | 월 20만 원 | 월 10만 원 |\n\n"
            "누리시는 월 15만 원 × 10개월 = 150만 원을 지원합니다.\n")
    assert report.fix_ledger_magnitudes(text, ASK) == text


def test_a_result_written_a_digit_off_is_still_restored() -> None:
    text = "누리시의 1인 최대 지원액은 1,500만 원입니다.\n"
    assert report.fix_ledger_magnitudes(text, ASK) == "누리시의 1인 최대 지원액은 150만 원입니다.\n"
