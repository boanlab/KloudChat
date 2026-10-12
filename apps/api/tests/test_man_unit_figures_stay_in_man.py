"""Figures the person wrote in 만 원 stay in 만 원: an operand that is the person's own digits is
not blown up to won (「편성 45,000,000만 원」), and a bare ratio operand never gains a second
「만」 (「강사비 4,200만만 원」)."""

from app.services import report, units

RESULT = ("- 예산: 편성 4,500만원 / 집행 4,215만원 (시상금 2,000 / 식음료 870 / "
          "장소·장비 임차 610 / 멘토·심사 수당 520 / 홍보·기념품 215, 단위 만원)")
PLAN = ("- 예산 총 1억 2천만원(시비 1억, 대학 자부담 2천만): 강사비 4,200만 / "
        "서포터즈 활동비 3,000만 / 교재·실습 재료 1,500만 / 장비 임차(태블릿 30대) 1,200만 / "
        "홍보 600만 / 운영 인건비 1,500만")


def test_a_man_unit_table_and_its_formulas_keep_the_person_s_digits():
    body = ("- 집행 현황(단위: 만 원)\n\n| 항목 | 집행액 | 비율 |\n|---|---|---|\n"
            "| 시상금 | 2,000 | 47.4% |\n| 합계 | 4,215 | 100.0% |\n\n"
            "- 편성 4,500만 원 / 집행 4,215만 원 / 잔액 285만 원(집행률 93.7%)\n"
            "  ※ 집행률 = 4,215 ÷ 4,500 × 100 ≈ 93.7%\n"
            "  ※ 잔액 = 4,500 - 4,215 = 285만 원")
    assert units.fix_magnitude_slips(units.fix_unit_labels(body, RESULT), RESULT) == body


def test_a_ratio_in_man_units_gains_no_second_man():
    para = ("강사비 4,200만 원은 1억 2천만 원의 35.0%(4,200 ÷ 12,000 × 100 = 35.0), "
            "교재·실습 재료 1,500만 원은 12.5%(1,500 ÷ 12,000 × 100 = 12.5), "
            "운영 인건비 1,500만 원은 12.5%(1,500 ÷ 12,000 × 100 = 12.5)를 차지합니다.")
    out = report.fix_restated_amounts(para, PLAN)
    assert "만만" not in out and out == para


def test_a_doubled_scale_word_left_by_any_step_is_restored_to_the_material_s_form():
    shipped = ("편성 45,000,000만 원 / 집행 42,150,000만 원, "
               "강사비 4,200만만 원, 재료 1,500만만만 원")
    assert units.restore_material_scale(shipped, RESULT + "\n" + PLAN) == (
        "편성 4,500만 원 / 집행 4,215만 원, 강사비 4,200만 원, 재료 1,500만 원")
