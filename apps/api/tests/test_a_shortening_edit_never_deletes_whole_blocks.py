"""「절반으로 줄여」 shortens: a rewrite that drops a part's table or list whole, or cuts far
more than the share asked, is refused (the office-like edit tool says so; the revision sends
it back, then keeps the part)."""

from __future__ import annotations

import pytest

from app.services import revise, web_doc_edit

BEFORE = ("일정은 다음과 같습니다. 3월에 설계, 4월에 구현, 5월에 시험을 합니다. "
          "예산은 3억 원입니다. "
          "설계는 외부 전문가 자문을 받아 진행하고, 구현은 내부 개발팀이 맡으며, 시험은 실제 "
          "사용자 30명이 참여하는 시범 운영으로 합니다. 각 단계의 결과는 주간 회의에서 "
          "점검합니다.\n\n"
          "| 단계 | 시기 |\n| --- | --- |\n| 설계 | 3월 |\n| 구현 | 4월 |\n\n"
          "- 첫째 고려 사항\n- 둘째 고려 사항\n- 셋째 고려 사항")


def test_a_shortening_that_drops_the_table_or_the_list_is_refused():
    no_table = BEFORE.split("\n\n| 단계")[0] + "\n\n- 첫째 고려 사항\n- 둘째 고려 사항"
    assert "표가 통째로" in revise.shortening_loss(BEFORE, no_table, "절반으로 줄여 줘")
    no_list = BEFORE.rsplit("\n\n- ", 1)[0]
    assert "목록이 통째로" in revise.shortening_loss(BEFORE, no_list, "2절을 줄여 줘")
    tiny = "| 단계 | 시기 |\n| --- | --- |\n| 설계 | 3월 |\n\n- 고려 사항"
    assert "%만 남습니다" in revise.shortening_loss(BEFORE, tiny, "절반으로 줄여 줘")


def test_a_fair_shortening_and_other_edits_pass():
    half = ("3월 설계, 4월 구현, 5월 시험. 예산 3억 원.\n\n"
            "| 단계 | 시기 |\n| --- | --- |\n| 설계 | 3월 |\n| 구현 | 4월 |\n\n"
            "- 첫째 고려 사항\n- 둘째 고려 사항")
    assert revise.shortening_loss(BEFORE, half, "절반으로 줄여 줘") is None
    assert revise.shortening_loss(BEFORE, "다른 글", "표를 빼 주세요 말고 고쳐 줘") is None


def test_the_section_tool_refuses_it():
    data = {"sections": [{"id": "a", "heading": "일정", "content": BEFORE}]}
    with pytest.raises(web_doc_edit.WebDocError, match="표가 통째로"):
        web_doc_edit.apply(data, "보고", [{"op": "set_section", "section": "s1",
                                           "text": "짧게 씀"}],
                           deck=False, request="1절을 절반으로 줄여 줘")
