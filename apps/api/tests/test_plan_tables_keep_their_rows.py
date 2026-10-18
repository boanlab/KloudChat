"""Plan tables that keep the material's rows and budget items."""

from app.services import table_integrity

MEMO = ("- 예산 총 1억 2천만원(시비 1억, 대학 자부담 2천만): 강사비 4,200만 / "
        "서포터즈 활동비 3,000만 / 교재·실습 재료 1,500만 / 장비 임차(태블릿 30대) 1,200만 / "
        "홍보 600만 / 운영 인건비 1,500만")

BUDGET = """총 예산 1억 2천만원은 시비 1억과 대학 자부담 2천만으로 구성합니다(표 4).

**표 4. 예산 계획**

| 항목 | 금액(만원) | 비율(%) | 산출 근거 |
|---|---|---|---|
| 강사비 | 4,200 | 35.0 | [단가] |
| 서포터즈 활동비 | 3,000 | 25.0 | [단가] |
| 교재·실습 재료 | 1,500 | 12.5 | [단가] |
| 장비 임차(태블릿 30대) | 1,200 | 10.0 | [단가] |
| 홍보 | 600 | 5.0 | [단가] |
| 합계 | 12,000 | 100.0 | 시비 10,000 + 대학 자부담 2,000 |"""

PROGRAMS = """아래 표는 세 프로그램의 대상, 내용, 규모를 비교한 것입니다.

표: 세부 프로그램의 대상, 내용, 규모

프로그램 | 대상 | 내용 | 규모

세 프로그램은 대상과 기능에서 구분됩니다."""


def test_a_budget_missing_a_listed_item_and_off_its_total_is_found():
    found = table_integrity.problems(BUDGET, MEMO)
    assert any("운영 인건비" in p for p in found)
    assert any("10,500" in p and "12,000" in p for p in found)


def test_a_header_with_no_rows_is_found():
    found = table_integrity.problems(PROGRAMS, MEMO)
    assert any("프로그램 | 대상 | 내용 | 규모" in p for p in found)


def test_the_missing_item_is_put_back_from_the_material_and_the_total_then_adds_up():
    mended = table_integrity.mend(BUDGET, MEMO)
    assert "| 운영 인건비 | 1,500 | 12.5 | [산출 근거] |" in mended
    assert mended.index("운영 인건비") < mended.index("| 합계 |")
    assert table_integrity.problems(mended, MEMO) == []


def test_a_header_left_without_rows_is_taken_out_with_its_caption():
    mended = table_integrity.mend(PROGRAMS, MEMO)
    assert "프로그램 | 대상" not in mended and "표: 세부 프로그램" not in mended
    assert mended.endswith("세 프로그램은 대상과 기능에서 구분됩니다.")


def test_whole_tables_are_left_alone():
    right = BUDGET.replace(
        "| 합계 |", "| 운영 인건비 | 1,500 | 12.5 | [단가] |\n| 합계 |")
    assert table_integrity.problems(right, MEMO) == []
    assert table_integrity.mend(right, MEMO) == right
    won = ("| 항목 | 금액 |\n|---|---|\n| 강사비 | 4,200만 원 |\n| 홍보 | 600만 원 |\n"
           "| 합계 | 4,800만 원 |")
    assert table_integrity.problems(won, "") == []
