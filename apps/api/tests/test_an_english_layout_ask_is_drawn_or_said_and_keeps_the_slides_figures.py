"""A layout asked in English or Korean (「as a numbered list」, 「목록으로」) is read as that
layout, and a slide still drawn otherwise is said as not done; figures a slide showed and its
rewrite left out are asked for once more, then said; an ask typed in English gets its reply in
English."""

from __future__ import annotations

import time

import pytest

from app.pipeline import revise_stages, save_stages
from app.pipeline.bundle import Document
from app.pipeline.stage import Context
from app.services import deck, output_language, revise

ASK = "Change slide 4 so it shows the three routing rules as a numbered list."
SLIDES = [
    {"id": "a", "layout": "title", "title": "On-Premise vs Cloud LLMs for Drafting"},
    {"id": "b", "layout": "bullets", "title": "Key Economic and Latency Metrics",
     "bullets": ["$4 vs $38 per 1k docs", "Latency: 9s local / 14s cloud"]},
    {"id": "c", "layout": "bullets", "title": "Benchmarks", "bullets": ["27B model: 82%"]},
    {"id": "d", "layout": "table", "title": "Critical Performance Limitation",
     "rows": [["Metric", "Local 27B", "Cloud Frontier"], ["Context >20 pages", "64%", "91%"],
              ["Drafting Score", "82%", "100%"]]},
    {"id": "e", "layout": "bullets", "title": "Hybrid Routing",
     "bullets": ["Route short docs locally; use cloud for long context."]},
]
#: the rewrite kept the table and put words where the figures were
STILL_TABLE = {**SLIDES[3], "rows": [["Criterion", "Local (27B)", "Cloud (Frontier)"],
                                    ["Long context", "Fails >20 pg", "Sustained"],
                                    ["Accuracy gap", "82% vs ~100%", "Baseline"]]}
NUMBERED = {"id": "d", "layout": "numbered", "title": "Critical Performance Limitation",
            "items": [["Long documents go to the cloud", "64% local vs 91% cloud past 20 pages"],
                      ["Short drafts stay local", "the 27B model reaches 82% of the frontier"],
                      ["Check the gap", "cloud scores 100%"]]}


@pytest.mark.parametrize("words, layout", [
    (ASK, "numbered"), ("Turn slide 3 into a table", "table"),
    ("Change slide 4 to a timeline.", "timeline"), ("show it as bullets", "bullets"),
    ("4장을 번호 목록으로 바꿔줘", "numbered"), ("4장을 목록으로 바꿔줘", "bullets"),
    ("글머리표로 바꿔줘", "bullets"), ("목록을 표로 바꿔줘", "table"),
    ("체크리스트로 바꿔줘", "checklist"), ("Add a row to the table", None),
    ("Put the list into slide 3", None),
])
def test_a_layout_is_read_from_english_and_korean_asks(words, layout):
    assert deck.requested_layout(words) == layout


def test_a_slide_still_drawn_as_a_table_is_said_as_not_a_numbered_list():
    assert deck.unmet_asks(STILL_TABLE, ASK) == ["번호 목록으로 바꾸기"]
    assert deck.unmet_asks(NUMBERED, ASK) == []
    # a numbered list is a list
    assert deck.unmet_asks(NUMBERED, "4장을 목록으로 바꿔줘") == []


def test_the_figures_a_rewrite_left_out_are_found():
    assert deck.lost_figures(SLIDES[3], STILL_TABLE, ASK) == ["64%", "91%"]
    assert deck.lost_figures(SLIDES[3], NUMBERED, ASK) == []
    # an ask that may take figures away
    assert deck.lost_figures(SLIDES[3], STILL_TABLE, "Shorten slide 4") == []
    started = time.monotonic()
    deck.lost_figures({"bullets": ["1" * 3 + " " * 50000 + "x"]}, {}, "")
    assert time.monotonic() - started < 0.5


def _bundle(typed: str):
    return revise_stages.bundle_for(
        request="Create a 5-slide English presentation", instruction=typed, model={"id": "m"},
        planner=None, api_key="k", is_deck=True, title="On-Premise vs Cloud LLMs", data={},
        parts=[dict(s) for s in SLIDES], material=[],
        plan=revise.Plan(scope="parts", targets=[3], note=typed), format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})


async def _rewrite(bundle):
    bundle.plan.order = [3]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    return bundle.document


@pytest.mark.asyncio
async def test_figures_left_out_are_asked_for_once_more_and_the_slide_keeps_them(monkeypatch):
    notes: list[str] = []
    answers = iter([STILL_TABLE, NUMBERED])

    async def rewrite_slide(**kwargs):
        notes.append(kwargs["note"])
        return dict(next(answers)), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    doc = await _rewrite(_bundle(ASK))
    assert "Keep every figure the slide shows now: 64%, 91%." in notes[1]
    assert doc.sections[3]["layout"] == "numbered" and doc.dropped == [] and doc.unmet == []
    assert save_stages.revision_reply(doc, None, is_deck=True, english=True) == (
        "Revised slide 4.")


@pytest.mark.asyncio
async def test_a_rewrite_that_still_drops_figures_and_the_asked_list_says_both(monkeypatch):
    async def rewrite_slide(**_kwargs):
        return dict(STILL_TABLE), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    doc = await _rewrite(_bundle(ASK))
    assert doc.dropped == ["4장 64%, 91%"] and doc.unmet == ["4장 번호 목록으로 바꾸기"]
    assert save_stages.revision_reply(doc, None, is_deck=True, english=True) == (
        "slide 4 as a numbered list did not show in the result. Please ask again. "
        "On slide 4, 64%, 91% dropped out of the rewrite; ask again to put them back if they "
        "belong there.")
    assert save_stages.revision_reply(doc, None, is_deck=True) == (
        "요청한 4장 번호 목록으로 바꾸기는 반영하지 못했습니다. 다시 요청해 주세요. "
        "4장에서 64%, 91%가 빠졌습니다. 필요하면 다시 넣어 달라고 해 주세요.")


def test_an_english_ask_gets_an_english_reply():
    assert output_language.typed_in_english("Make slide 3 a table.")
    assert not output_language.typed_in_english("3장을 table로 바꿔줘")
    doc = Document(removed=["4장의 차트", "2절의 표 2"], touched=[0, 2, 3, 4], changed=5,
                   missed=["그림 3"])
    assert save_stages.revision_reply(doc, revise.Plan(summary=3), is_deck=True,
                                      english=True) == (
        "Removed the chart on slide 4, Table 2 from section 2. Put a 3-line summary at the "
        "top. Revised slides 1, 3–5. However, Figure 3 could not be found in the document. "
        "Please ask again.")


#: the slide before and after a numbered-list rewrite (reverify-j d1)
GAP = {"id": "d", "layout": "bullets", "title": "The Long-Context Quality Gap",
       "bullets": ["Local model failed often past 20 pages", "Local: 64 percent",
                   "Cloud: 91 percent", "A 27 point gap"]}
GAP_LISTED = {"id": "d", "layout": "numbered", "title": "The Long-Context Quality Gap",
              "items": [["27B local model", "82% of the frontier score"],
                        ["Short documents", "$4 vs $38 per 1k docs"],
                        ["Documents above 20 pages", "64% vs 91%"]]}


def test_only_figures_really_gone_are_lost_however_they_are_written():
    # 「64 percent」 is 「64%」 in a list item; 「27 point」 is not 「27B」
    assert deck.lost_figures(GAP, GAP_LISTED, ASK) == ["27 point"]
    assert deck.lost_figures({"bullets": ["매출 420만원, 3.5%p 상승"]},
                             {"bullets": ["매출 420만 원", "3.5 포인트 올랐다"]}) == []
    assert deck.lost_figures({"bullets": ["$4 per 1k docs, 9s"]},
                             {"bullets": ["4 docs"]}) == ["$4", "1k", "9s"]
    # 「64 vs 91」: 「vs」 is no unit
    assert deck.lost_figures({"bullets": ["64 vs 91"]}, {"rows": [["64%", "91%"]]}) == []


T1_PARTS = ["1. 이번 주 업무 요약 및 성과", "2. 주간 주요 업무 추진 현황",
            "3. 이슈 및 협조 요청 사항", "4. 다음 주 업무 계획"]
T1_ASK = "표 2와 그림 1은 빼고, 맨 앞에 3줄 요약을 넣어줘."


def test_numbered_removals_with_asks_naming_their_parts_need_no_planner():
    texts = ["요약", "표 1 표 2", "표 3", "표 4"]
    assert revise.settled_by_code(T1_ASK, T1_PARTS, texts)
    assert revise.settled_by_code("표 2는 빼 줘", T1_PARTS, texts)
    # a structure change, or an ask naming no part: the planner reads it
    assert not revise.settled_by_code("표 2는 빼고 2절을 둘로 나눠줘", T1_PARTS, texts)
    assert not revise.settled_by_code("표 2는 빼고 전체적으로 더 간결하게", T1_PARTS, texts)
    assert not revise.settled_by_code("맨 앞에 3줄 요약을 넣어줘.", T1_PARTS, texts)


@pytest.mark.asyncio
async def test_a_removal_and_a_summary_go_to_the_edit_not_to_a_new_outline(monkeypatch):
    from types import SimpleNamespace

    from app.models.workspace import ArtifactKind
    from app.routers import sessions as sessions_router

    sections = [{"heading": h, "content": f"{h} 본문"} for h in T1_PARTS]

    class Db:
        async def get(self, _model, _id):
            return SimpleNamespace(title="주간 업무 보고", kind=ArtifactKind.report,
                                   data={"sections": sections})

    async def planner(**_kwargs):
        # the reading that regenerated the whole report
        return revise.Plan(scope="outline", note="요약 절을 맨 앞에 추가")

    monkeypatch.setattr(revise, "plan", planner)
    plan, _ = await sessions_router._revision_plan(
        Db(), SimpleNamespace(artifact_id="a1"),
        instruction=T1_ASK, model={"id": "m"}, api_key="k")
    assert plan is not None and not plan.restructures and plan.targets == []
