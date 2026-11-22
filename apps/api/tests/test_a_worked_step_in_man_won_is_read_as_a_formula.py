"""A written step in 만/억 (「490만 원 - 75만 원 = 415만 원」) is read as a formula the guard
checks."""

from __future__ import annotations

import datetime as dt

from app.services import check_skills, value_guard

REQUEST = ("월세 220만 원, 인건비 월 190만 원, 기타 고정비 월 80만 원, "
           "음료 객단가 6,000원(원가율 30%), "
           "도서 판매 월 300만 원 예상(마진 25%), 공간 대여 월 60만 원 예상.")
STEPS = ("- 월 고정비: 220만 원 + 190만 원 + 80만 원 = 490만 원\n"
         "- 도서 마진: 300만 원 × 25% = 75만 원\n"
         "- 음료로 채울 고정비: 490만 원 - 75만 원 - 60만 원 = {rest}\n")


def _ground(text: str) -> str:
    options = {**check_skills.options_for(dt.date(2026, 11, 21), {}, surface="document"),
               "person": REQUEST}
    return check_skills.ground(("grounding-check",), text, material=REQUEST, computed=[],
                               options=options, evidence=[REQUEST], worked=text).text


def test_a_right_step_in_man_won_keeps_its_result():
    text = STEPS.format(rest="355만 원")
    assert _ground(text) == text


def test_a_wrong_step_in_man_won_is_still_marked():
    out = _ground(STEPS.format(rest="365만 원"))
    assert f"365만 원{value_guard.MARK}" in out


def test_a_scaled_formula_reads_as_plain_figures():
    [(expr, operands, stated, _)] = value_guard._formulas("490만 원 - 75만 원 - 60만 원 = 355만 원")
    assert stated == 3550000 and operands == ["4900000", "750000", "600000"]
    assert eval(expr) == 3550000  # noqa: S307
