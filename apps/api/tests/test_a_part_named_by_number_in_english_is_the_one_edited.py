"""A slide or section the person names by number, in English too (「slide 4」, 「slides 2-3」,
「the 4th slide」), is the one edited whatever the planner picked; a list item's heading is
cut at a word, with Latin text given more room; a sentence pointing at a removed table by
place (「아래 표와 같습니다」) goes with it."""

from __future__ import annotations

import time

from app.services import revise, slide_patterns

SLIDES = ["Title", "Cost", "Latency", "Operational cost and latency benchmarks", "Hybrid Routing"]
ASK = "Change slide 4 so it shows the three routing rules as a numbered list."


def test_a_slide_named_in_english_is_read():
    assert revise.named_slides(ASK) == {4}
    assert revise.named_slides("Tighten slides 2-3.") == {2, 3}
    assert revise.named_slides("Rewrite the 4th slide") == {4}
    assert revise.named_slides("Shorten the second slide") == {2}
    assert revise.named_slides("4장을 표로 바꿔줘") == {4}
    # each slide reads its own clause
    assert revise.slide_clause("Make slide 2 a table. Shorten slide 5.", 4) == "Shorten slide 5."
    started = time.monotonic()
    revise.named_slides("slide" + " " * 50000 + "x")
    assert time.monotonic() - started < 0.5


def test_the_named_slide_is_the_target_whatever_the_planner_picked():
    picked = revise.Plan(scope="parts", targets=[4], note="routing rules as a list")
    assert revise.numbered_target(picked, SLIDES, ASK).targets == [3]
    assert revise.numbered_target(picked, SLIDES, "3장을 표로 바꿔줘").targets == [2]
    # a number past the deck, a structure change, or no number: the planner's pick stands
    assert revise.numbered_target(picked, SLIDES, "Fix slide 9").targets == [4]
    assert revise.numbered_target(picked, SLIDES, "20장으로 늘려줘").targets == [4]
    assert revise.numbered_target(picked, SLIDES, "Make the routing slide a list").targets == [4]


def test_a_section_named_in_english_is_its_ask():
    parts = ["1. 요약", "2. 현황", "3. 이슈"]
    asks = revise.section_asks("Shorten section 2.", parts, ["a", "b", "c"])
    assert asks is not None and asks[0] == {1: ["Shorten section 2."]}


def test_a_list_heading_is_cut_at_a_word_and_latin_gets_more_room():
    items = slide_patterns.clean_pairs("numbered", [
        ["Route short, routine drafts to the local model", "below 20 pages"],
        ["Escalate long-context documents to cloud", "above 20 pages"],
        ["짧은 일상 문서는 로컬 모델로 보내고 긴 문서는 클라우드로", "20쪽 미만"]])
    heads = [item[0] for item in items]
    assert heads[0] == "Route short, routine drafts"
    assert heads[1] == "Escalate long-context documents"
    assert heads[2] == "짧은 일상 문서는 로컬 모델로 보내고 긴"


def test_a_sentence_pointing_at_a_removed_table_by_place_goes_with_it():
    parts = [{"id": "s0", "heading": "1. 이슈",
              "content": "대성유통 협상 시 선택 가능한 안과 조건은 아래 표와 같습니다.\n\n"
                         "**표 1. 단가 안**\n\n| 안 | 인하율 |\n| --- | --- |\n| A | 4% |\n\n"
                         "물류팀 협의가 필요합니다. 위 표현은 확정이 아닙니다."}]
    out, taken, _ = revise.drop_numbered(parts, [("표", 1)])
    assert taken == ["1절의 표 1"]
    assert out[0]["content"] == "물류팀 협의가 필요합니다. 위 표현은 확정이 아닙니다."
