"""Joining one slide's items (「2장 항목을 하나로 합쳐줘」, "Merge the items on slide 4 into one")
is that slide's edit done by code, not a new deck; 「Shrink to 3 slides」 is read like 「3장으로
줄여줘」; a question or a named edit typed while a question card is open is not its answer; the
figures a drawn diagram shows count as shown."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.models.chat import SessionKind
from app.pipeline import revise_stages
from app.pipeline.stage import Context
from app.services import deck, internal_words, revise

POINTS = {"id": "p", "layout": "bullets", "title": "출고 지연 원인",
          "bullets": ["이전 기간 4일 간 출고 지연", "신규 거래처 3곳 확보", "4분기 목표 0일"]}


@pytest.mark.parametrize("words, restructure", [
    ("2장 항목을 하나로 합쳐줘", False), ("Merge the items on slide 4 into one", False),
    ("Merge the bullets on slide 4 into one bullet", False), ("4장과 5장을 합쳐줘", True),
    ("Merge slides 4 and 5", True), ("Shrink to 3 slides", True), ("4장을 표로 바꿔줘", False),
])
def test_joining_one_slides_items_is_not_a_new_structure(words, restructure):
    assert revise.asks_restructure(words) is restructure


def test_a_merge_of_one_slides_items_targets_that_slide():
    assert revise.numbered_parts("2장 항목을 하나로 합쳐줘", ["a", "b", "c"]) == [1]
    assert revise.numbered_parts("Merge the items on slide 3 into one", ["a", "b", "c"]) == [2]
    assert deck.items_asked("Merge the items on slide 4 into one") == 1
    assert deck.items_asked("merge them into a single bullet") == 1


def test_an_english_count_ask_reshapes_the_deck():
    plan = revise.count_change(revise.Plan(scope="parts", targets=[]), ["a"] * 6,
                               "Shrink to 3 slides")
    assert plan.scope == "outline" and "3장" in plan.note
    assert revise.count_change(revise.Plan(scope="parts"), ["a"] * 6,
                               "Make slide 3 shorter").scope == "parts"
    started = time.monotonic()
    revise.count_change(revise.Plan(scope="parts"), ["a"] * 6, "shrink" + " a" * 25000)
    revise.asks_restructure("merge " + " " * 50000 + "items")
    deck.items_asked("into" + " " * 50000 + "one")
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_items_joined_on_one_slide_are_joined_by_code(monkeypatch):
    async def rewrite_slide(**_kwargs):
        raise AssertionError("joining items is not rewritten")

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    bundle = revise_stages.bundle_for(
        request="보고", instruction="2장 항목을 하나로 합쳐줘", model={"id": "m"}, planner=None,
        api_key="k", is_deck=True, title="보고", data={},
        parts=[{"id": "t", "layout": "title", "title": "보고"}, dict(POINTS)], material=[],
        plan=revise.Plan(scope="parts", targets=[1], note="합치기"), format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    slide = bundle.document.sections[1]
    assert slide["bullets"] == [
        "이전 기간 4일 간 출고 지연 · 신규 거래처 3곳 확보 · 4분기 목표 0일"]
    assert bundle.document.dropped == []


def _session(stage="clarify"):
    return SimpleNamespace(pending={"stage": stage, "questions": [{"id": "q"}]},
                           artifact_id="a1", kind=SessionKind.slides)


def _payload(text):
    return SimpleNamespace(content=text, approve=False, answers=None)


def test_a_question_or_a_named_edit_while_a_card_is_open_is_not_its_answer():
    from app.routers import sessions

    assert sessions._sets_card_aside(_session(), _payload("4장 수치는 어디서 나온 거야?"))
    assert sessions._sets_card_aside(_session(), _payload("4장을 번호 목록으로 바꿔줘"))
    assert not sessions._sets_card_aside(_session(), _payload("4장과 5장"))
    assert not sessions._sets_card_aside(_session(), _payload("모두 한 장으로"))
    # a question leaves any card, a proposed outline too
    assert sessions._sets_card_aside(_session("plan"), _payload("4장 수치는 어디서 나온 거야?"))


def test_a_drawn_diagrams_figures_are_shown():
    old = [{"id": "s", "layout": "bullets", "title": "조치",
            "bullets": ["출고 지연 4일", "목표 0일"]}]
    new = [{"id": "s", "layout": "bullets", "title": "조치", "bullets": [],
            "diagram": {"source": "flowchart LR\n  a[출고 지연 4일] --> e[4분기 목표: 0일]\n"
                        "  x[3\n%]", "caption": "지연 원인"}}]
    assert deck.lost_across(old, new) == []
    assert deck.lost_figures(old[0], new[0]) == []


def test_a_label_line_describing_the_table_is_a_leak():
    text = "| 사업 | 상태 |\n|---|---|\n| CRM | 진행 중 |\n\n상표: 업무 현황을 정리한 표"
    kept = internal_words.strip_work_notes(text, request="", document=True)
    assert "상표" not in kept and "| CRM | 진행 중 |" in kept
    assert "구분: 매출" in internal_words.strip_work_notes("구분: 매출이 늘었다.", request="",
                                                          document=True)
    started = time.monotonic()
    internal_words.strip_work_notes("상표:" + " " * 50000 + "표", request="", document=True)
    assert time.monotonic() - started < 0.5
