"""The end of a dashed span written without its year (「2026-11-03~11-10」) is a day of the
material, so it stays unmarked."""

from __future__ import annotations

import datetime as dt

from app.services import check_skills

TODAY = dt.date(2026, 11, 21)


def _ground(material: str, answer: str) -> check_skills.Outcome:
    options = {**check_skills.options_for(TODAY, {}, surface="report"), "requirements": True}
    return check_skills.ground(("grounding-check",), answer, material=material,
                               computed=[], options=options, evidence=[material])


def test_the_span_end_is_the_materials_day():
    memo = ("시설 점검 계획 메모 (2026-12-01 작성)\n"
            "3. 정밀 점검(2026-12-07~12-11, 3개 동), 결과 보고는 별도.")
    answer = "정밀 점검은 2026년 12월 7일부터 12월 11일까지 3개 동을 대상으로 진행합니다."
    assert _ground(memo, answer).text == answer


def test_a_span_into_the_next_year_ends_in_that_year():
    memo = "연말 휴무 안내: 사무실 휴무(2026-12-28~01-02), 비상 연락은 당직자에게."
    answer = "사무실은 2026년 12월 28일부터 2027년 1월 2일까지 쉽니다."
    assert _ground(memo, answer).text == answer


def test_a_day_the_material_lacks_is_still_marked():
    memo = "시설 점검 계획 메모 (2026-12-01 작성)\n3. 정밀 점검(2026-12-07~12-11, 3개 동)."
    answer = "정밀 점검은 2026년 12월 7일부터 12월 18일까지 진행합니다."
    assert "확인 필요" in _ground(memo, answer).text
