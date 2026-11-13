"""A report's chat edit fills its head's fields (작성일, 작성 부서, 작성자) by hand and moves a
chapter in place; neither rewrites a section."""

from __future__ import annotations

import pytest

from app.pipeline import revise_stages
from app.pipeline.stage import Context
from app.services import revise

SECTIONS = [
    {"id": "a", "heading": "개요", "content": "개요 본문"},
    {"id": "b", "heading": "시장", "content": "시장 본문"},
    {"id": "c", "heading": "위험 요인", "content": "위험 본문"},
    {"id": "d", "heading": "일정", "content": "일정 본문"},
]
HEAD = {"format": "result_report", "fields": [["작성일", ""], ["작성 부서", ""], ["작성자", ""]]}


def _bundle(typed: str):
    return revise_stages.bundle_for(
        request="결과 보고서", instruction=typed, model={"id": "m"}, planner=None, api_key="k",
        is_deck=False, title="결과 보고", data={"sections": SECTIONS, "titleBlock": HEAD},
        parts=[dict(s) for s in SECTIONS], material=[],
        plan=revise.Plan(scope="parts", targets=[], note=typed), format_id="result_report",
        usage={"inputTokens": 0, "outputTokens": 0})


async def _route(bundle):
    return [e async for e in revise_stages.Route().run(bundle, Context())]


@pytest.mark.asyncio
async def test_the_head_fields_are_filled_by_hand():
    bundle = _bundle("작성일은 2026. 11. 13., 작성 부서는 기획팀, 작성자는 홍길동으로 해 줘")
    await _route(bundle)
    assert bundle.document.data["titleBlock"]["fields"] == [
        ["작성일", "2026. 11. 13."], ["작성 부서", "기획팀"], ["작성자", "홍길동"]]
    assert bundle.document.by_hand and bundle.plan.revision.targets == []
    assert bundle.document.sections == SECTIONS


@pytest.mark.asyncio
async def test_a_chapter_moves_in_place():
    bundle = _bundle("위험 요인 장을 1장 뒤로 옮겨 줘")
    await _route(bundle)
    assert [s["id"] for s in bundle.document.sections] == ["a", "c", "b", "d"]
    assert bundle.document.by_hand and bundle.plan.revision.targets == []
    assert [s["content"] for s in bundle.document.sections] == [
        "개요 본문", "위험 본문", "시장 본문", "일정 본문"]


def test_numbers_name_parts_for_a_swap_too():
    assert revise.order_swap("4장과 2장 순서를 바꿔 줘", [s["heading"] for s in SECTIONS]) == (3, 1)


def test_a_part_nobody_asked_about_keeps_its_figures():
    from app.pipeline.save_stages import keep_untouched

    bundle = _bundle("2절의 시장 규모를 다듬어 줘")
    bundle.request.turn["before_parts"] = [dict(s, content=f"{s['content']} 120억")
                                           for s in SECTIONS]
    bundle.plan.order = [1]
    rewritten = [dict(s, content=f"{s['content']} 120억") for s in SECTIONS]
    rewritten[1] = dict(rewritten[1], content="시장은 크다")  # asked: may change
    rewritten[3] = dict(rewritten[3], content="일정은 미정")  # not asked: figure lost
    parts, kept = keep_untouched(bundle, rewritten)
    assert parts[1]["content"] == "시장은 크다"
    assert parts[3]["content"] == "일정 본문 120억" and kept == ["4절 「일정」"]
    # a fact the person changed, carried into another part, stays
    bundle = _bundle("매출을 150억으로 바꿔 줘")
    bundle.request.turn["before_parts"] = [dict(s, content=f"{s['content']} 120억")
                                           for s in SECTIONS]
    bundle.plan.order = [0]
    carried = [dict(s, content=f"{s['content']} 120억") for s in SECTIONS]
    carried[2] = dict(carried[2], content="위험 본문 150억")
    parts, kept = keep_untouched(bundle, carried)
    assert parts[2]["content"] == "위험 본문 150억" and kept == []


@pytest.mark.asyncio
async def test_a_theme_colour_goes_to_the_design_not_into_the_text():
    bundle = _bundle("남색 테마로 바꿔 줘")
    await _route(bundle)
    assert bundle.document.data["design"]["accent"] == "#1F3A68"
    assert bundle.document.sections == SECTIONS and bundle.document.by_hand


def test_a_diagram_asked_for_is_drawn_not_described():
    from app.pipeline.revise_stages import figure_note

    assert "```mermaid```" in figure_note("2절에 업무 흐름 도식을 넣어 주세요")
    assert figure_note("2절을 두 문장으로 줄여 줘") == ""
