"""Computed values reach the writer as people write them, not as `:.4f` or `:.4g` prints them.

The calculation pass was told 「유효숫자 4자리」, so its programs printed 「평균 만족도 = 3.2600 점」
and 「1년 총매출 = 1.369e+04만원」, and writers copied both into reports (round-1 r1r03,
r1r04 on both arms: 「12개월 누적 매출은 1.369e+04만 원」, a month table of 「7.744e+06원」)."""

from __future__ import annotations

from app.services import calc


def test_padding_zeros_come_off_and_real_decimals_stay() -> None:
    out = calc._lines("사용 시간 증가율 = 50.0000 %\n온라인 판매 목표 = 36000.0000 대\n"
                      "무게 감소율 = 15.7895 %\n단가 = 0.5000 만 원\n합계 = 1,716 만 원\n")
    assert out == ["사용 시간 증가율 = 50 %", "온라인 판매 목표 = 36000 대",
                   "무게 감소율 = 15.7895 %", "단가 = 0.5 만 원", "합계 = 1,716 만 원"]


def test_a_table_row_line_is_unpadded_cell_by_cell() -> None:
    out = calc._lines("수도권1팀 | 합계 = 24900.0000 만원 | 달성률 = 103.7500 %\n")
    assert out == ["수도권1팀 | 합계 = 24900 만원 | 달성률 = 103.75 %"]


def test_a_value_in_exponent_notation_never_reaches_the_writer() -> None:
    out = calc._lines("1년 총매출 = 1.369e+04만원\n3월 | 매출 = 7.744e+06원\n"
                      "1년 총영업이익 = -9115만원\n")
    assert out == ["1년 총영업이익 = -9115만원"]


def test_the_calculation_prompt_asks_for_plain_numbers() -> None:
    assert "유효숫자 4자리" not in calc.PROMPT
    assert "지수 표기" in calc.PROMPT


def test_the_document_s_size_and_setting_are_no_data_to_calculate() -> None:
    # round-1 r1d02 (11-20): 「1학년 … 3주차 … 12장 … 예제 1개 … 퀴즈 3문항」 read as four
    # figures; the calculation pass made up its own demand and supply lines and both arms
    # showed an equilibrium (16원, 68개) no slide's equations give
    lecture = ("대학교 1학년 경제학원론 3주차 강의 「수요와 공급」 슬라이드를 12장 정도로 만들어 줘. "
               "간단한 계산 예제 1개(수요식·공급식으로 균형 가격·수량 구하기), 마지막에 퀴즈 3문항.")
    assert not calc.needed(lecture, [])
    priced = "월 기본료 6만 원, 흑백 3,000장 포함, 초과 장당 10원, 연간 총비용을 계산해 줘. 4대 기준."
    assert calc.needed(priced, [])
