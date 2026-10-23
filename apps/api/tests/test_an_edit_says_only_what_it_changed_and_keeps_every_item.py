"""Slide and section edits as reverify-y found them: a caveat written in another form is still
the caveat, a label is not cut inside its brackets, a join on a slide with nothing to join is
said and changes nothing, steps are regrouped rather than rewritten, and a lost count names
what it counts."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.pipeline import revise_stages, save_stages
from app.pipeline.stage import Context
from app.services import deck, revise

#: reverify-y t1 2절 before and after 「2절을 두 문장으로 줄여줘」
SECTION = ("대성유통이 요청한 단가 인하율 5%는 현재 허용 가능한 4%를 초과하고 있습니다.\n\n"
           "5%를 수용할 경우 경쟁사 A사가 동일 품목으로 4% 낮은 단가를 제시한 것으로 "
           "파악되었으나, 이 정보는 아직 확정되지 않았습니다.\n\n"
           "물류팀의 영남 출고 지연 재발 우려도 이번 주 처리가 필요한 사안입니다. 한솔마트 반품 "
           "420만원의 원인이 9월 물류센터 이전 시 출고 지연으로 파악되고 있으나, 물류팀으로부터 "
           "SLA 협의 일정이 확정되지 않아 재발 방지 여건을 확인할 수 없습니다.")
SHORT = ("대성유통의 5% 단가 인하 요구가 허용 가능한 4%를 초과하고 있어, 경쟁사 A사의 4% 제시가 "
         "확정되지 않은 상황에서 2차 미팅 전에 인하 폭 결재를 확정해야 합니다. 또한 한솔마트 반품 "
         "420만원의 원인인 9월 영남 출고 지연이 재발하지 않도록, 물류팀 SLA 협의 일정을 다음 주 "
         "내로 확인해야 합니다.")


def test_a_caveat_kept_in_another_form_is_not_said_lost():
    assert revise.lost_facts(SECTION, SHORT) == ["이번 주"]
    for kept in ("A사의 4% 제시는 확정 전입니다.", "A사의 4% 제시는 미확정입니다.",
                 "A사의 4% 제시는 아직 결정되지 않았습니다.", "A사의 4% 제시는 미정입니다."):
        assert revise.lost_facts("A사의 4% 제시는 확정되지 않았습니다.", kept) == []
    dropped = revise.lost_facts("A사의 4% 제시는 확정되지 않았습니다.", "A사는 4%를 제시했습니다.")
    assert dropped == ["A사 4%의 「확정 아님」 표시"]
    started = time.monotonic()
    revise.lost_facts("확정" + " " * 50000 + "전", "미정" + " " * 50000)
    assert time.monotonic() - started < 0.5


def test_a_mark_left_off_is_said_in_its_own_clause():
    doc = SimpleNamespace(removed=[], missed=[], unmet=[], touched=[1], changed=1, failed=[],
                          counted=[], already=[], added=[], unlisted=[],
                          dropped=["2절 이번 주, SLA의 「확정 아님」 표시"])
    assert save_stages.revision_reply(doc, None, is_deck=False) == (
        "2절을 고쳤습니다. 2절에서 이번 주가 빠졌고, SLA의 「확정 아님」 표시도 빠졌습니다. "
        "필요하면 다시 넣어 달라고 해 주세요.")
    doc.dropped = ["2절 SLA의 「확정 아님」 표시"]
    assert save_stages.revision_reply(doc, None, is_deck=False) == (
        "2절을 고쳤습니다. 2절에서 SLA의 「확정 아님」 표시가 빠졌습니다. "
        "필요하면 다시 넣어 달라고 해 주세요.")


def test_a_numbered_list_does_not_cut_a_label_inside_its_brackets():
    metrics = {"id": "m", "layout": "metrics", "title": "핵심 성과 지표 요약",
               "metrics": [["128억", "3분기 매출"],
                           ["9.6억", "영업이익 (이익률 7.5%, 전분기 6.8%)"], ["4일", "출고 지연"]]}
    assert deck.reshaped(metrics, "numbered")["items"] == [
        ["3분기 매출", "128억"], ["영업이익", "9.6억 (이익률 7.5%, 전분기 6.8%)"],
        ["출고 지연", "4일"]]


#: reverify-y kd2 7장
STEPS = {"id": "s", "layout": "steps", "title": "4분기 핵심 실행 과제",
         "steps": [["정비", "영남 물류센터 시스템 안정화"], ["확보", "추가 거래처 발굴 및 계약"],
                   ["관리", "4분기 매출 140억 달성 모니터링"]]}


def test_steps_are_regrouped_into_the_count_asked_with_every_step_kept():
    two = deck.joined_items(STEPS, deck.items_asked("7장 항목을 두 개로 나눠줘"))
    assert two["layout"] == "steps" and two["steps"] == [
        ["정비 · 확보", "영남 물류센터 시스템 안정화 · 추가 거래처 발굴 및 계약"],
        ["관리", "4분기 매출 140억 달성 모니터링"]]
    one = deck.joined_items(STEPS, deck.items_asked("7장 항목을 하나로"))
    assert one["layout"] == "bullets" and "steps" not in one and one["bullets"] == [
        "정비: 영남 물류센터 시스템 안정화 · 확보: 추가 거래처 발굴 및 계약 · "
        "관리: 4분기 매출 140억 달성 모니터링"]
    assert deck.lost_figures(STEPS, one) == []
    assert deck.joined_items(STEPS, 3) is None and deck.items_held(STEPS) == 3


PIE = {"id": "p", "layout": "chart-pie", "title": "지역별 매출 구성 및 특징",
       "chart": {"kind": "pie", "unit": "억 원", "categories": ["수도권", "영남", "호남", "기타"],
                 "series": [{"name": "매출 비중", "values": [71.0, 33.0, 14.0, 10.0]}]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("slide, ask, said", [
    (PIE, "2장 항목을 하나로", "2장에는 합치거나 나눌 항목 목록이 없어 그대로 두었습니다."),
    (STEPS, "2장 항목을 세 개로", "2장은 이미 항목이 3개여서 그대로 두었습니다."),
    (PIE, "Merge the items on slide 2 into one",
     "Slide 2 has no list of items to join or split, so it was kept as it was."),
])
async def test_a_join_with_nothing_to_join_is_said_and_changes_nothing(monkeypatch, slide, ask,
                                                                       said):
    async def rewrite_slide(**_kwargs):
        raise AssertionError("nothing to join is not rewritten")

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    bundle = revise_stages.bundle_for(
        request="보고", instruction=ask, model={"id": "m"}, planner=None,
        api_key="k", is_deck=True, title="보고", data={},
        parts=[{"id": "t", "layout": "title", "title": "보고"}, dict(slide)], material=[],
        plan=revise.Plan(scope="parts", targets=[1], note="합치기"), format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})
    bundle.plan.order = [1]
    events = []
    with pytest.raises(revise_stages.Stop):
        async for event in revise_stages.Rewrite().run(bundle, Context()):
            events.append(event)
    assert bundle.document.sections[1] == slide and not bundle.document.changed
    assert "".join(e.get("text", "") for e in events if e["type"] == "delta") == said


def test_a_lost_korean_count_is_named_with_what_it_counts():
    old = [{"id": "a", "layout": "bullets", "title": "성과",
            "bullets": ["이에 물류 프로세스를 재설계했고, 신규 거래처 3곳 확보",
                        "중복 거래처 37건 발견", "매출 128억"]},
           {"id": "b", "layout": "bullets", "title": "해결", "bullets": ["신규 거래처 3곳 선점"]}]
    new = [{"id": "a", "layout": "bullets", "title": "성과", "bullets": ["매출 128억"]}]
    assert deck.lost_across(old, new) == ["신규 거래처 3곳", "중복 거래처 37건"]
    # a sum is said as written
    gone = deck.lost_across(old, [{"id": "c", "layout": "bullets", "bullets": ["x"]}])
    assert gone[-1] == "128억"
