"""Values the code tool computed from the person's file are the answer's grounds: never marked
「(확인 필요)」, never sent back as hand-worked, and no paragraph auditing the run reaches the
reader."""

from __future__ import annotations

from app.services import agent, internal_words

#: two runs over a sales CSV: branch totals with return rates, then a month × branch pivot
_OUTPUTS = [
    "stdout:\n"
    "=== (2) 지점별 집계 ===\n"
    "지점       매출_합계  판매수량_합계  반품수량_합계      반품률\n"
    "강남 210905000.0     3360      108 0.032143\n"
    "=== (3) 상품군별 집계 ===\n"
    "상품군 매출_합계 판매수량_합계 평균_단가\n"
    "가전 500760000.0 2877 174056.31\n"
    "=== (9) 반품률 (%) ===\n"
    "지점별:\n"
    "  강남: 3.10%\n"
    "  광주: 3.31%\n",
    "stdout:\n"
    "=== (1) 지점별 월별 매출 합계 ===\n"
    "월            7           8           9\n"
    "지점\n"
    "강남  77272000.0  58277000.0  75356000.0\n"
    "광주  31839000.0  38072000.0  45563000.0\n"
    "(2) 증감률:\n"
    "  강남 7→8: -24.58%, 8→9: 29.31%\n",
]


def test_a_figure_the_code_printed_in_another_table_is_not_marked():
    answer = "8월 지점별 값(강남 58,277,000원, 광주 38,072,000원)은 7월보다 줄었습니다."
    checked, fixes = agent.code_checked(answer, _OUTPUTS, mark=True)
    assert fixes == [] and checked == answer
    # a value no run printed is still marked against the one it names
    wrong, fixes = agent.code_checked("지점별 강남은 4.10입니다.", _OUTPUTS, mark=True)
    assert "(확인 필요)" in wrong and fixes


def test_a_printed_fall_and_a_grouped_decimal_are_not_hand_worked_values():
    answer = "강남은 7→8월 24.58% 줄었고 가전 평균단가는 174,056.31원입니다. 새 값 6.50도 있습니다."
    assert agent._unverified_figures(answer, "\n".join(_OUTPUTS)) == ["6.50"]


def test_a_paragraph_auditing_this_turns_run_does_not_reach_the_reader():
    answer = (
        "3분기 매출은 9월에 꺾였습니다.\n\n"
        "한 가지 밝히면, 화면에 떠 있는 두 차트는 이 턴의 코드 출력에 없는 값으로 그려진 것입니다. "
        "차트 안의 8월 값은 이번 실행 결과와 맞지 않습니다. "
        "차트를 다시 그려야 정확한 선이 나옵니다.\n\n"
        "정리하면 가전 의존도가 높습니다."
    )
    out = internal_words.strip_work_notes(answer, request="첨부한 매출 데이터를 요약해 줘")
    assert out == "3분기 매출은 9월에 꺾였습니다.\n\n정리하면 가전 의존도가 높습니다."
    # a game's turn is no audit
    game = "이번 턴에는 비숍으로 공격하세요.\n\n다음 수는 상대 반응을 봅니다."
    assert internal_words.strip_work_notes(game, request="체스 다음 수 알려줘") == game
