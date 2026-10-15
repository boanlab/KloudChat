"""A strong writer gets only what it cannot know; a small one keeps its guard rails (round 7:
the same strong model wrote worse inside KloudChat than called directly)."""

from __future__ import annotations

import copy
import json

import pytest

from app.models.chat import SessionKind
from app.routers import sessions
from app.services import (
    agent,
    context,
    deck,
    freshness,
    model_tier,
    prompt_tier,
    red_flags,
    report,
)
from app.services.tools.base import SearchEvidence, Tool, ToolContext, ToolResult

STRONG = "deepseek/deepseek-v4-pro-0813"
SMALL = "local/qwen3.8-27b"


@pytest.mark.parametrize(("model", "strong"), [
    (STRONG, True), ("qwen/qwen3.8-max-0902", True), ("anthropic/claude-sonnet-5", True),
    (SMALL, False), ("strict-local/qwen3.8-27b", False), ("deepseek/deepseek-v4.1-flash", False),
    (None, False),
])
def test_the_tier_follows_the_writer(model, strong) -> None:
    assert model_tier.strong(model) is strong
    assert prompt_tier.light(model) is strong


# ── chat ────────────────────────────────────────────────────────────────


def _chat(light: bool) -> str:
    return context.system_prompt(SessionKind.chat, with_tools=True, web_search=True,
                                 web_search_auto=True, light=light)


def test_a_strong_writer_gets_no_writing_rules_in_chat() -> None:
    light, full = _chat(True), _chat(False)
    # what it cannot know: the date, the tools and how a citation becomes a link
    assert context._today() in light
    assert "[1]" in light and "use_skill" in light and "do not copy URLs" in light
    assert prompt_tier.ANTI_FABRICATION in light
    # not how to write
    for rule in (context._WRITING, context._CHAT_TASK_CONTRACT, context._CORE_ACCURACY,
                 freshness.FRESHNESS_INSTRUCTION, context._WEB_SEARCH_AUTO):
        assert rule not in light
        assert rule in full
    assert len(light) * 8 < len(full)


def test_build_messages_passes_the_tier_on() -> None:
    history = [{"role": "user", "content": "안녕"}]
    light = context.build_messages(SessionKind.chat, history, light=True)[0]["content"]
    full = context.build_messages(SessionKind.chat, history)[0]["content"]
    assert context._WRITING in full and context._WRITING not in light


def test_the_answer_policy_is_short_for_a_strong_writer_and_the_emergency_rule_stays() -> None:
    messages = [{"role": "system", "content": _chat(True)}, {"role": "user", "content": "x"}]
    light = freshness.with_answer_policy(messages, {"id": STRONG}, advice="legal",
                                         current_fact=True, urgent="suicide",
                                         light=True)[0]["content"]
    full = freshness.with_answer_policy(messages, {"id": STRONG}, advice="legal",
                                        current_fact=True, urgent="suicide")[0]["content"]
    assert freshness.ADVICE_INSTRUCTION.format(domain="legal") not in light
    assert freshness.LIGHT_ADVICE_INSTRUCTION.format(domain="legal") in light
    assert freshness.CURRENT_FACT_INSTRUCTION not in light
    assert freshness.FRESHNESS_INSTRUCTION not in light
    # the closing notice the screen expects is still offered
    assert freshness.accuracy_caveat("한국어", {"id": STRONG}, STRONG) in light
    # red flags: the same for every writer
    assert red_flags.instruction("suicide") in light
    assert red_flags.instruction("suicide") in full


def test_a_small_writer_may_draw_a_table_and_state_assumptions() -> None:
    """r7 P20 (a grid asked for, prose only) and A10 (no estimate without given numbers):
    the full rules no longer forbid either."""
    full = _chat(False)
    assert "use a table" in full
    assert "Do not guess numbers that are not given" not in full
    assert "a rough range or a typical price the user asks for" in full


def test_search_on_keeps_established_knowledge_for_a_small_writer() -> None:
    """r7 P27: well-known history is not dropped because the results miss it."""
    nudge = context.system_prompt(SessionKind.chat, with_tools=True, web_search=True)
    assert "Well-established background" in nudge
    assert "Do not assert items you could not verify by search" not in nudge


# ── documents ───────────────────────────────────────────────────────────


def _section(model: str) -> list[dict]:
    prompt = report._SECTION_PROMPT.format(
        heading="시장 규모", outline="1. 시장 규모", written="(nothing yet)", refs="(none)",
        request="AI 튜터 시장의 TAM·SAM·SOM을 추정해 줘", role="본문", blocks="", others="",
        facts=report._facts_line("AI 튜터 시장의 TAM·SAM·SOM을 추정해 줘", []), genre="",
    )
    return context.build_document_messages(SessionKind.report, prompt, model=model)


def test_a_strong_report_writer_keeps_only_the_structure_rules() -> None:
    light, full = _section(STRONG), _section(SMALL)
    assert context._WRITING not in light[0]["content"]
    assert freshness.FRESHNESS_INSTRUCTION not in light[0]["content"]
    assert freshness.FRESHNESS_INSTRUCTION in full[0]["content"]
    prompt = light[-1]["content"]
    # what the renderer and the pipeline read
    for kept in ("No heading line", "「표: 제목」", "[[그림:", "source's number", "「~합니다」"):
        assert kept in prompt
    # how to write: gone, one line against invention instead
    for gone in ("Prose is the default", "Use only the figures in", "Write at the level",
                 "Never invent a specific value", "Do not leave a blank for a figure"):
        assert gone not in prompt
        assert gone in full[-1]["content"]
    assert prompt.count(prompt_tier.ANTI_FABRICATION) == 1
    # the no-figures line is said, not ruled
    assert "of any kind" not in prompt and "쓸 수 있는 수치: none" in prompt


def test_the_full_section_rules_allow_a_stated_assumption_for_an_estimate() -> None:
    """r7 A10-T2: 「Never invent … ratios」 beat 「state an assumption」, and no TAM came out."""
    rules = report._SECTION_PROMPT
    assert "ratios, causes of failure" not in rules
    assert "TAM·SAM·SOM" in rules and "stated 「가정」 with their basis" in rules
    assert "stated 「가정」 with their basis" in report._DRAFT_PROMPT
    assert "estimate" in report._facts_line("AI 튜터 시장 규모를 추정해 줘", [])


def test_a_strong_writer_keeps_the_block_syntax_of_the_draft() -> None:
    light = prompt_tier.for_writer(report._DRAFT_PROMPT, light=True)
    assert "```kpi" in light and "```callout" in light and "Prose is the default" not in light
    assert "## 제목" in light


def test_a_strong_deck_writer_keeps_the_json_fields() -> None:
    light = prompt_tier.for_writer(deck._DRAFT_PROMPT, light=True)
    for kept in ('"rows"', '"timeline"', '"notes"', "Answer with a JSON object only",
                 "Do not use emoji"):
        assert kept in light
    for gone in ("Figures only from the usable figures", "On a slide that teaches rules"):
        assert gone not in light
    assert prompt_tier.for_writer(deck._DRAFT_PROMPT, light=False) == deck._DRAFT_PROMPT


def test_a_persons_own_bullet_is_not_dropped() -> None:
    """Only bullets opening with a rule are dropped (with their continuation lines); the
    person's bullet that quotes one later in the line stays."""
    text = ("Rules:\n- Prose is the default: usually two\n  paragraphs.\n\n요청:\n"
            "- 표로 정리 (Prose is the default: usually 말고)")
    assert prompt_tier.for_writer(text, light=True) == (
        "Rules:\n- " + prompt_tier.ANTI_FABRICATION
        + "\n\n요청:\n- 표로 정리 (Prose is the default: usually 말고)")


def test_the_step_log_names_the_light_setup() -> None:
    step = sessions._setup_step(STRONG)
    assert step["label"] == prompt_tier.SETUP_LABEL
    assert step["detail"].startswith("light setup") and STRONG in step["detail"]


# ── the tool loop ───────────────────────────────────────────────────────

_URL = "https://example.test/strike"


def _found() -> ToolResult:
    return ToolResult(content=f"[1] Great Unrest\n{_URL}\nDockers struck in 1911.",
                      search_evidence=SearchEvidence((_URL,)))


def _nothing() -> ToolResult:
    return ToolResult(content="'q' 에 대한 검색 결과가 없습니다.", detail="0개 결과", empty=True)


def _search(results: list[ToolResult]) -> Tool:
    async def run(_arguments):
        return results.pop(0)

    return Tool(name="web_search", description="search", parameters={"type": "object"},
                run=run, label="Search")


def _scripted(monkeypatch, steps: list[dict], seen: list) -> None:
    async def stream(_model, messages, _tools, *_args, **_kwargs):
        seen.append(copy.deepcopy(messages))
        step = steps[min(len(seen), len(steps)) - 1]
        acc = agent._Accumulator()
        acc.content = [step["text"]] if step.get("text") else []
        acc.calls = dict(enumerate(step.get("calls", [])))
        acc.usage = {"inputTokens": 1, "outputTokens": 1}
        for text in acc.content:
            yield "delta", text
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 8)


def _call(n: int, text: str = "") -> dict:
    return {"text": text, "calls": [{"id": f"s{n}", "name": "web_search",
                                     "arguments": json.dumps({"query": f"q{n}"})}]}


async def _turn(model: str, request: str, tool: Tool) -> list[dict]:
    return [e async for e in agent.run_turn(
        model, [{"role": "system", "content": "시스템"}, {"role": "user", "content": request}],
        [tool], ToolContext(user_id="u", session_id="s", api_key="k", request=request))]


def _shown(events: list[dict]) -> str:
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            text = text.replace(event["text"], "", 1)
    return text


def _loop_notes(seen: list) -> list[str]:
    return list(dict.fromkeys(str(m["content"]) for s in seen for m in s[2:]
                              if m["role"] == "user"))


@pytest.mark.asyncio
async def test_empty_searches_do_not_use_up_the_cap(monkeypatch) -> None:
    """r7 P31: four 0-result searches and the cap note, then a Fermi estimate refused."""
    seen: list = []
    _scripted(monkeypatch, [_call(1), _call(2), _call(3), _call(4),
                            {"text": "가정: 가구 약 400만 … 약 400명으로 추정합니다."}], seen)
    await _turn(SMALL, "서울에 피아노 조율사가 몇 명일지 추정해 줘", _search([_nothing()] * 4))
    assert len(seen) == 5
    assert _loop_notes(seen) == []


@pytest.mark.asyncio
async def test_the_cap_note_lets_the_writer_reason_and_assume(monkeypatch) -> None:
    seen: list = []
    _scripted(monkeypatch, [_call(1), _call(2), _call(3), {"text": "답입니다 [1]."}], seen)
    await _turn(SMALL, "서울에 피아노 조율사가 몇 명일지 추정해 줘", _search([_found()] * 3))
    notes = _loop_notes(seen)
    assert len(notes) == 1 and notes[0].startswith("웹 검색은 충분히 했습니다.")
    assert "아는 것과 지금까지 찾은 자료로" in notes[0] and "가정을 밝히고" in notes[0]
    assert "state the assumptions" in agent._SEARCH_CAP_EN


@pytest.mark.asyncio
async def test_a_runaway_of_empty_searches_still_stops(monkeypatch) -> None:
    seen: list = []
    _scripted(monkeypatch, [_call(n) for n in range(1, 7)] + [{"text": "모르겠습니다."}], seen)
    await _turn(SMALL, "x를 찾아 줘", _search([_nothing()] * agent.MAX_SEARCH_CALLS))
    assert len(seen) == agent.MAX_SEARCH_CALLS + 1
    assert _loop_notes(seen)[0].startswith("웹 검색은 충분히 했습니다.")


@pytest.mark.asyncio
async def test_a_deep_searching_skill_gets_twice_the_searches(monkeypatch) -> None:
    """r10 SK-competitor-2: three competitors, and the cap note after the third search."""
    seen: list = []
    _scripted(monkeypatch, [_call(n) for n in range(1, 7)] + [{"text": "답입니다 [1]."}], seen)
    request = "세 경쟁사를 비교해 줘"
    events = [e async for e in agent.run_turn(
        SMALL, [{"role": "system", "content": "시스템"}, {"role": "user", "content": request}],
        [_search([_found()] * 6)], ToolContext(user_id="u", session_id="s", api_key="k",
                                               request=request), deep_search=True)]
    assert len(seen) == 7 and _shown(events)
    assert _loop_notes(seen)[0].startswith("웹 검색은 충분히 했습니다.")
    assert agent.MAX_WEB_SEARCHES < 6


@pytest.mark.asyncio
async def test_a_deep_search_gets_a_search_more_for_each_thing_the_request_lists(
        monkeypatch) -> None:
    """r10 LFLF6 SK-company-3: six searches spent before two of four memo lines were looked up."""
    seen: list = []
    _scripted(monkeypatch, [_call(n) for n in range(1, 9)] + [{"text": "답입니다 [1]."}], seen)
    request = "더존비즈온 조사해 줘.\n\n- ERP 1위라고 들음\n- 공공 클라우드 한다고 함"
    events = [e async for e in agent.run_turn(
        SMALL, [{"role": "system", "content": "시스템"}, {"role": "user", "content": request}],
        [_search([_found()] * 8)], ToolContext(user_id="u", session_id="s", api_key="k",
                                               request=request), deep_search=True)]
    assert len(seen) == 9 and _shown(events)
    assert _loop_notes(seen)[0].startswith("웹 검색은 충분히 했습니다.")


@pytest.mark.asyncio
async def test_a_deep_search_answer_is_held_once_for_a_listed_name_never_searched(
        monkeypatch) -> None:
    """r10 SK-competitor-1: 잔디·플로우 searched, 네이버웍스 never, and the answer written."""
    def ask(n: int, query: str) -> dict:
        return {"text": "", "calls": [{"id": f"s{n}", "name": "web_search",
                                       "arguments": json.dumps({"query": query})}]}

    seen: list = []
    _scripted(monkeypatch, [ask(1, "잔디 요금제"), ask(2, "플로우 요금제"),
                            {"text": "표입니다 [1]."}, ask(3, "네이버웍스 요금제"),
                            {"text": "세 곳 표입니다 [1]."}], seen)
    request = "협업툴 비교표 만들어 줘. 잔디, 플로우, 네이버웍스를 대상으로 정리해 줘."
    events = [e async for e in agent.run_turn(
        SMALL, [{"role": "system", "content": "시스템"}, {"role": "user", "content": request}],
        [_search([_found()] * 3)], ToolContext(user_id="u", session_id="s", api_key="k",
                                               request=request), deep_search=True)]
    notes = _loop_notes(seen)
    assert any("네이버웍스" in n and "아직 한 번도 검색하지 않았습니다" in n for n in notes)
    assert "세 곳 표입니다" in _shown(events) and "표입니다 [1]." not in _shown(events).replace(
        "세 곳 표입니다 [1].", "")
    # without deep search the answer stands as written
    seen.clear()
    _scripted(monkeypatch, [ask(1, "잔디 요금제"), {"text": "표입니다 [1]."}], seen)
    await _turn(SMALL, request, _search([_found()]))
    assert len(seen) == 2


def test_the_grounding_note_keeps_established_knowledge() -> None:
    """r7 P27: the 1911 London dock strike dropped as 「확인 못 함」 next to Liverpool hits."""
    for light in (False, True):
        note = agent.search_grounding_instruction(light=light)
        assert "history" in note and "textbook" in note
        assert "Do not add names, numbers, dates, or current status absent" not in note
    assert len(agent.search_grounding_instruction(light=True)) < 400


@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "note"), [
    (STRONG, agent._LIGHT_SEARCH_GROUNDING), (SMALL, agent._SEARCH_GROUNDING_INSTRUCTION)])
async def test_the_grounding_note_follows_the_tier(monkeypatch, model, note) -> None:
    seen: list = []
    _scripted(monkeypatch, [_call(1), {"text": "1911년 부두 파업이 있었습니다 [1]."}], seen)
    await _turn(model, "20세기 초 런던 파업을 정리해 줘", _search([_found()]))
    assert note in seen[1][0]["content"]


@pytest.mark.asyncio
async def test_a_fixed_form_gets_no_source_list_or_grounding_paragraphs(monkeypatch) -> None:
    """r7 P48: 「조언만 답하고, 다른 건 쓰지 마」 got a 「### 출처」 list and reasons."""
    seen: list = []
    _scripted(monkeypatch, [_call(1), {"text": "- 현금 합의는 거절하세요 [1]."}], seen)
    request = "교통사고가 났어. 조언만 답하고, 다른 건 쓰지 마. 설명도 쓰지 마."
    events = await _turn(STRONG, request, _search([_found()]))
    shown = _shown(events)
    assert f"[[1]]({_URL})" in shown
    assert "### 출처" not in shown and "### Sources" not in shown
    assert agent._FORM_FIXED_GROUNDING.strip() in seen[1][0]["content"]


@pytest.mark.asyncio
async def test_a_short_preamble_with_a_table_stays(monkeypatch) -> None:
    """r7 A05-T1: the item table written before the calculator call was taken back and the
    answer began 「합계: …」."""
    table = ("부산은 광역시라 숙박비 상한은 1박 80,000원입니다.\n\n| 항목 | 금액 |\n|---|---|\n"
             "| 숙박 | 160,000원 |\n| 교통 | 119,600원 |")
    assert len(table) < agent._NARRATION_CHARS
    seen: list = []
    _scripted(monkeypatch, [_call(1, table), {"text": "\n\n합계: 279,600원입니다."}], seen)
    events = await _turn(SMALL, "출장비 정산해 줘", _search([_found()]))
    shown = _shown(events)
    assert shown.startswith(table) and "합계: 279,600원" in shown


def test_pure_narration_is_still_narration() -> None:
    for line in ("검색해 보겠습니다.", "2026년 요금을 확인해 보겠습니다."):
        assert agent._pure_narration(line, request="요금 알려 줘")
    assert not agent._pure_narration("| 항목 | 금액 |\n|---|---|", request="")
    assert not agent._pure_narration("숙박비 상한은 80,000원입니다.", request="")


@pytest.mark.asyncio
async def test_punctuation_between_written_calls_is_not_shown(monkeypatch) -> None:
    """r7 A11-T2: two calls written as text left 「\\n, \\n\\n」 on screen before the answer."""
    def call(q: str) -> str:
        return (f"<tool_call>web_search<arg_key>query</arg_key><arg_value>{q}</arg_value>"
                "</tool_call>")

    seen: list = []
    _scripted(monkeypatch, [{"text": f"\n{call('a')}\n, {call('b')}\n\n"},
                            {"text": "# 배틀카드\n본문 [1]."}], seen)
    events = await _turn(STRONG, "배틀카드 만들어 줘", _search([_found(), _found()]))
    shown = [e["text"] for e in events if e["type"] == "delta"]
    assert all(t.strip(" \n,") for t in shown)
    assert _shown(events).startswith("# 배틀카드")


@pytest.mark.asyncio
async def test_a_paragraph_after_the_writers_list_brings_no_second_list(monkeypatch) -> None:
    """r7 A11-T1: 「## 출처」, then a paragraph, then the server's 「### 출처」 once more."""
    answer = (f"부두 파업 [1].\n\n## 출처\n- [1] [Great Unrest]({_URL})\n\n"
              "보완이 필요한 항목: 정원 [1].")
    seen: list = []
    _scripted(monkeypatch, [_call(1), {"text": answer}], seen)
    shown = _shown(await _turn(SMALL, "런던 파업 정리해 줘", _search([_found()])))
    assert shown.count("출처") == 1
    assert "보완이 필요한 항목" in shown


def test_a_bare_double_bracket_citation_is_linked() -> None:
    """r7 P19: 「[[2]][[3]]」 copied from the earlier linked answer stayed raw."""
    linked, cited = agent._link_citations("인상 가능성 [[1]][[2]].", ["https://a.example/1",
                                                                  "https://b.example/2"])
    assert linked == "인상 가능성 [[1]](https://a.example/1)[[2]](https://b.example/2)."
    assert cited == [1, 2]


@pytest.mark.asyncio
async def test_the_search_step_records_its_query(monkeypatch) -> None:
    """r7 DSP P07·P31·P56: 0-result searches with no record of what was searched."""
    seen: list = []
    _scripted(monkeypatch, [_call(1), {"text": "모르겠습니다."}], seen)
    events = await _turn(SMALL, "x를 찾아 줘", _search([_nothing()]))
    done = [e for e in events if e["type"] == "step" and e.get("status") == "done"
            and e.get("id", "").startswith("h")]
    assert done and done[0]["query"] == "q1" and done[0]["detail"] == "0개 결과"
