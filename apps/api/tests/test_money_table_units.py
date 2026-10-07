"""A money table's unit label follows the amounts the person gave."""

from app.services import units

ASK = (
    "연구원 인건비 4,800만 원, 장비비 1,200만 원, 연구재료비 600만 원, 연구활동비 300만 원이고 "
    "간접비는 직접비의 15%야."
)


def test_a_table_labelled_in_millions_for_amounts_in_ten_thousands_is_relabelled():
    table = (
        "| 구분 | 금액 (백만 원) |\n|---|---:|\n| 인건비 | 4,800 |\n| 장비비 | 1,200 |\n"
        "| 재료비 | 600 |\n| 합계 | **6,900** |"
    )
    out = units.fix_unit_labels(table, ASK)
    assert out.startswith("| 구분 | 금액 (만 원) |") and "| 인건비 | 4,800 |" in out


def test_a_right_label_and_unrelated_tables_are_left_alone():
    right = "단위: 만 원\n\n| 구분 | 금액 |\n|---|---|\n| 인건비 | 4,800 |\n| 장비비 | 1,200 |"
    assert units.fix_unit_labels(right, ASK) == right
    won = "| 구분 | 금액 (원) |\n|---|---|\n| 인건비 | 48,000,000 |\n| 장비비 | 12,000,000 |"
    assert units.fix_unit_labels(won, ASK) == won
    plain = "| 주차 | 내용 |\n|---|---|\n| 1 | 소개 |"
    assert units.fix_unit_labels(plain, ASK) == plain


MARKETER = "광고 노출 200만 회, 클릭률 1.5%, 전환율 3%, 객단가 45,000원, 광고비 3,000만 원이야."


def test_an_amount_copied_a_power_of_ten_off_is_put_right_with_its_results():
    answer = (
        "ROAS는 매출 4,050만 원을 광고비 3,000만 원으로 나눈 **13.5**입니다.\n\n"
        "| 지표 | 계산 | 값 |\n|---|---|---|\n"
        "| ROAS | 40,500,000 ÷ 3,000,000 | 13.5 |\n"
        "| CPA | 3,000,000 ÷ 900 | 약 33,333원 |\n\n"
        "ROAS 13.5는 광고비 1원당 매출 13.5원이 발생한 것입니다."
    )
    out = units.fix_magnitude_slips(answer, MARKETER)
    assert "40,500,000 ÷ 30,000,000 | 1.35 |" in out
    assert "30,000,000 ÷ 900 | 약 33,333원" in out
    assert "13.5" not in out and "1원당 매출 1.35원" in out


def test_correct_calculations_are_left_alone():
    right = "| ROAS | 40,500,000 ÷ 30,000,000 | 1.35 |\n클릭 2,000,000 × 0.015 = 30,000회"
    assert units.fix_magnitude_slips(right, MARKETER) == right
