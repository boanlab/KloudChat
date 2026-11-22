"""The result of a written formula whose arithmetic code confirms is backed wherever the answer
repeats it, not marked 「(확인 필요)」."""

from __future__ import annotations

import datetime as dt

from app.services import check_skills

TODAY = dt.date(2026, 11, 21)
ASK = ("목돈 480만 원이 있어. 1년 뒤 세후 이자가 더 많은 쪽을 계산해 줘(이자소득세 15.4%).\n"
       "① 480만 원을 1년 정기예금(연 3.2%, 만기 일시 지급)\n"
       "② 480만 원을 파킹통장(연 2.0%, 월 단위 단리)에 두고 "
       "매달 1일 40만 원씩 12개월 적금(연 3.5%, 단리)에 납입")


def _ground(answer: str) -> str:
    options = {**check_skills.options_for(TODAY, {}, surface="chat"),
               "person": ASK, "requirements": True}
    return check_skills.ground(("grounding-check",), answer, material=ASK, computed=[],
                               options=options, evidence=[ASK]).text


ANSWER = """**① 정기예금이 세후 129,945.6원으로 ②보다 15,735.6원 많습니다.**

① 정기예금
- 세전 이자: 4,800,000 × 3.2% = 153,600원
- 세후: 153,600 × (1 − 0.154) = **129,945.6원**

② 파킹통장 + 12개월 적금
- 파킹통장 이자: 매달 1일 40만 원이 빠져나가므로 그 달의 잔액은 440만, 400만 … 0원입니다.
  400,000 × (2.0%/12) × (11+10+…+1+0 = 66) = 44,000원
- 적금 이자: 1회차는 12개월, 12회차는 1개월씩 붙으므로 개월 수 합이 78입니다.
  400,000 × 3.5% × (78/12) = 91,000원
- 세전 합계: 44,000 + 91,000 = 135,000원
- 세후: 135,000 × (1 − 0.154) = **114,210원**

| 구분 | 세전 이자 | 세후 이자 |
|---|---|---|
| ① 정기예금 | 153,600원 | 129,945.6원 |
| ② 파킹통장+적금 | 135,000원 | 114,210원 |
| 차이 | 18,600원 | 15,735.6원 |
"""


def test_confirmed_results_stay_unmarked_in_the_table_too():
    out = _ground(ANSWER)
    for figure in ("91,000원", "135,000원", "114,210원", "18,600원", "15,735.6원"):
        assert f"{figure}(확인 필요)" not in out, figure


def test_a_formula_on_a_figure_nobody_gave_is_still_marked():
    answer = "③ 적금 이자: 650,000 × 3.5% × (78/12) = 147,875원입니다.\n"
    assert "확인 필요" in _ground(answer)
