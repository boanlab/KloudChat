"""A compound Korean amount a source states (「3만2070원」, 「5조4000억원」) is read whole, so it is
not
marked 「(확인 필요)」."""

from __future__ import annotations

from app.services import report

PAGE = ("월 보험료는 A손해보험이 2만9140원으로 가장 낮았고 "
        "B화재 3만1260원, C손해보험 3만6020원 순이었다. "
        "국내 반려용품 시장은 지난해 4조1000억원에서 2027년 5조3000억원으로 커질 전망이다. "
        "정부 지원 예산은 1억 2,000만 원이다.")


def test_amounts_the_page_states_stay_unmarked() -> None:
    text = ("월 보험료는 A손해보험 2만9140원, B화재 3만1260원, C손해보험 3만6020원입니다[2].\n\n"
            "시장은 4조1000억원에서 5조3000억원으로 커질 전망이며[2], "
            "지원 예산은 1억 2,000만 원입니다.\n")
    assert report._without_invented_money(text, [PAGE]) == text


def test_an_amount_no_page_states_is_still_marked() -> None:
    out = report._without_invented_money("D화재는 월 4만2500원입니다.\n", [PAGE])
    assert out == "D화재는 월 4만2500원(확인 필요)입니다.\n"


def test_each_amount_is_read_whole() -> None:
    found = [m.group(0) for m in report._MONEY.finditer(
        "3만2070원, 5조 4,000억 원, 1억 2,000만 원, 15만 원, 1,850원")]
    assert found == ["3만2070원", "5조 4,000억 원", "1억 2,000만 원", "15만 원", "1,850원"]
