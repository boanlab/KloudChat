"""A chart's unit is kept whole; a limit the material only asks approval for is not written as
the current one."""

from __future__ import annotations

import time

from app.services import deck, stated_policy

#: the weekly memo: the 4% is a request for approval, no limit
MEMO = ("- 목: 경쟁사 A사가 동일 품목 4% 낮은 단가 제시한 것으로 파악됨(확정 아님).\n"
        "- 요청사항: 단가 인하 폭 결재 필요(최대 4%까지 허용 여부), "
        "물류팀 출고 SLA 협의 자리 마련.")


def test_a_limit_stated_beside_an_ask_in_another_clause_is_found():
    sentence = ("대성유통이 요구한 5% 인하와 현재 허용 한도인 최대 4% 사이에 차이가 있으므로, "
                "어느 범위까지 승인할지 결재가 필요합니다.")
    assert stated_policy.untraced(sentence, MEMO) == ["허용 한도"]
    # the ask about the limit itself, a condition, a marked sentence: left alone
    assert not stated_policy.untraced("허용 한도를 4%로 둘지 결정해 주십시오.", MEMO)
    assert not stated_policy.untraced("최대 4%까지 인하를 허용할지 결재를 요청합니다.", MEMO)
    assert not stated_policy.untraced("허용 한도가 4%라면, 요구와 1%p 차이가 납니다.", MEMO)
    assert not stated_policy.untraced(f"{sentence[:-1]} (확인 필요).", MEMO)
    assert not stated_policy.untraced("| 단가 인하 폭 결재 요청 | 최대 4% 허용 한도 여부 |", MEMO)
    assert not stated_policy.untraced("확인 필요: 2차 미팅 전까지 팀장님의 인하 한도 승인.", MEMO)
    # each clause settling a limit is found, wherever the ask stands
    assert stated_policy.untraced("현재 허용 범위는 4%이고, 5% 수용 여부는 결재가 필요합니다.",
                                  MEMO) == ["허용 범위"]


def test_a_clause_split_over_many_spaces_finishes_at_once():
    spaces = " " * 50_000
    start = time.perf_counter()
    stated_policy.untraced(f"허용 한도{spaces},{spaces}할지{spaces}", MEMO)
    deck._clean_chart({"unit": f"$ per{spaces}docs{spaces}", "categories": ["a", "b"],
                       "series": [{"name": "x", "values": [1, 2]}]})
    assert time.perf_counter() - start < 0.5


def test_a_chart_unit_is_kept_whole_or_to_its_first_word():
    chart = {"kind": "bar", "unit": "$ per 1,000 docs", "categories": ["Local", "Cloud"],
             "series": [{"name": "Cost", "values": [4, 38]}]}
    assert deck._clean_chart(chart)["unit"] == "$ per 1,000 docs"
    chart["unit"] = "$ per 1,000 generated documents in total"
    assert deck._clean_chart(chart)["unit"] == "$"
