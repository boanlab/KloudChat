"""A multiplier on a slide traces to the material like any figure (a ratio of two given figures
of one measure is worked out); a shrink keeps a finding's page count, puts one old item's parts
back together in every column, and names no lost figure the old deck only showed unchecked; an
edit's caveat loss names only what the hedge qualified."""

from __future__ import annotations

import datetime as dt
import time

from app.pipeline.check_stages import ground_slides, trace_slides
from app.services import check_skills, deck, revise, value_guard

#: the English conference deck's request: its findings are the material
TALK = (
    "Create a 8-slide English presentation for a conference talk: 'Can small on-premise "
    "language models replace cloud APIs for document drafting?' Findings: a 27B local model "
    "scored 82% of a frontier cloud model on a 40-task drafting benchmark (rated by 3 reviewers), "
    "cost per 1,000 documents was $4 vs $38, latency 9s vs 14s, but failed more on long-context "
    "tasks (64% vs 91% above 20 pages). Recommendation: hybrid routing."
)


def _options() -> dict:
    return {**check_skills.options_for(dt.date(2026, 10, 22), surface="deck"), "person": TALK,
            "requirements": True}


def test_an_invented_multiplier_on_a_slide_is_marked_and_a_worked_ratio_is_not() -> None:
    slides = [
        {"id": "a", "layout": "bullets", "title": "The Long-Context Performance Gap",
         "bullets": ["long-context failure at scale", "94x prompt costs VRAM not speed"]},
        {"id": "b", "layout": "bullets", "title": "Cost",
         "bullets": ["Cloud costs 9.5x more per 1k docs", "a 1920x1080 chart, 3 x 4 grid"]},
    ]
    assert trace_slides(slides, material=TALK, computed=[""], options=_options()) == {0: ["94x"]}
    out, found = ground_slides(slides, material=TALK, computed=[""], options=_options())
    assert [f["text"] for f in found] == ["94x"]
    assert out[0]["bullets"][1].startswith("94x(확인 필요)")
    assert out[1] == slides[1]


def test_the_value_guard_reads_a_multiplier_as_a_ratio_not_a_sum() -> None:
    ev = value_guard.Evidence.of([TALK])
    # 91 + 3 = 94: a sum of the figures is no multiplier
    assert not value_guard.supported("94x", "94x prompt costs VRAM", 0, ev, 0)
    assert value_guard.supported("9.5x", "costs 9.5x more", 6, ev, 0)


def test_a_shrink_keeps_a_page_count_a_finding_is_stated_against() -> None:
    kept = revise.without_counts(TALK)
    assert "8-slide" not in kept
    assert "(64% vs 91% above 20 pages)" in kept
    korean = revise.without_counts(
        "보고서를 10장으로 만들어줘. 계약서가 20쪽 이상이면 지연이 생긴다.")
    assert "10장" not in korean and "20쪽 이상이면" in korean
    start = time.perf_counter()
    revise.without_counts("above" + " " * 50_000 + "20 pages")
    assert time.perf_counter() - start < 0.5


def test_one_old_items_parts_spread_over_the_columns_go_back_together() -> None:
    old = [{"layout": "bands", "title": "Benchmark Setup",
            "bands": [["Models", "27B local vs frontier cloud, quantized Q4"],
                      ["Tasks", "40 document-drafting prompts, three human raters"]]}]
    new = [{"layout": "table", "title": "Benchmark Setup & Cost",
            "rows": [["Metric", "On-Prem LLM", "Cloud API"],
                     ["Model", "27B Q4 local", "Frontier cloud"],
                     ["Tasks", "40 prompts", "3 raters"],
                     ["Cost /1k docs", "$4", "$38"]]}]
    rows = revise.spread_rows(new, old)[0]["rows"]
    assert rows[2] == ["Tasks", "40 prompts, 3 raters", "40 prompts, 3 raters"]
    # each column's own value stays
    assert rows[1] == ["Model", "27B Q4 local", "Frontier cloud"]
    assert rows[3] == ["Cost /1k docs", "$4", "$38"]


def test_a_shrink_names_no_lost_figure_the_old_deck_only_showed_unchecked() -> None:
    old = [{"layout": "bullets", "title": "Gap",
            "bullets": ["94x (needs checking) prompt costs VRAM", "$4 vs $38 per 1k docs"]}]
    new = [{"layout": "bullets", "title": "Gap", "bullets": ["VRAM is the limit"]}]
    assert deck.lost_across(old, new) == ["$4", "$38", "1k docs"]


def test_a_hedge_qualifies_its_own_stretch_not_a_name_after_the_comma() -> None:
    old = ("목요일 대성유통 견적서를 발송했고, 경쟁사 A사가 동일 품목 4% 낮은 단가를 제시한 것으로 "
           "파악되었으나 아직 확정되지 않았습니다.\n\n영남 지역 출고 지연 재발 우려에 대해서는 "
           "물류팀 확인이 필요한 상태로, CRM 병합 완료는 다음 주 수요일 예정입니다(그림 1).")
    new = "이번 주에는 대성유통 견적서를 발송했습니다. CRM 중복 거래처 병합은 60% 진행되었습니다."
    said = revise._with_caveats(["4%", "A사"], old, new)
    assert said == ["4%", "A사(확정 아님)"]
    assert not any("CRM" in x for x in said)
    # a hedge closing the clause still qualifies what came before the comma
    assert revise._hedged("경쟁사 A사 단가는 4% 낮은 것으로, 확정되지 않았습니다.")[0][1] == ["A사"]
