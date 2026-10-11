"""A table cell whose amounts were taken out does not keep the bare words around them.

The research lane's seminar deck showed 「검토 , LLM」: 「검토 150건, LLM 5개」 lost both
amounts (not in the request as written) and kept the nouns and the comma.
"""

from __future__ import annotations

from app.services.deck import _facts_set, ground_cells


def test_items_left_bare_go_with_their_amounts():
    given = "ClaimCheck는 논문 300편, 요약 1,200건을 썼다."
    rows = [
        ["기준", "ClaimCheck", "GroundRev"],
        ["데이터 규모", "논문 300편, 요약 1,200건", "검토 150건, LLM 5개"],
        ["비고", "요약 1,200건, 전문가 4명 검수", "크롤러에서 95건 오류 발생"],
    ]
    out = ground_cells(rows, _facts_set(given), given)
    assert out[1] == ["데이터 규모", "논문 300편, 요약 1,200건", "—"]
    # A sentence keeps its words; an item that still says something stays.
    assert out[2] == ["비고", "요약 1,200건, 전문가 검수", "크롤러에서 오류 발생"]
