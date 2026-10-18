"""Leaks kept from the reader: block fences, tool calls as answers, marks in numbers, language."""

from __future__ import annotations

import json
import re

import pytest

from app.pipeline import chat_stages, registry
from app.routers import bot_api
from app.services import (
    agent,
    block_fences,
    copy_check,
    internal_words,
    output_language,
    report,
    value_guard,
)
from app.services.tools.base import Tool, ToolContext, ToolResult

# ── 1. block fences in the Markdown export ───────────────────────────────────

DISCOUNT_SECTION = (
    "B안의 15% 할인은 연 단위 계약 시에만 적용되는 이용료 할인입니다.\n\n"
    "```callout\n비교 전제\n두 안 모두 40명 기준 월 단가로 산정하며, B안의 15% 할인은 이용료에만 "
    "적용되고 초기 전환 비용 300만원은 제외됩니다.\n```\n\n"
    "## 대안 비교\n\n"
    "```kpi\n3,840,000원 | A안 연간 비용(첫해·이듬해 동일)\n9,720,000원 | B안 첫해 총비용\n"
    "5,880,000원 | 첫해 기준 A안 절감액\n60.0% | 파일 버전 혼선 불만 비율(21명/35명)\n```\n\n"
    "## 권고안과 다음 단계\n\n추천 안은 A안입니다."
)


def test_the_markdown_export_writes_callout_and_kpi_as_markdown() -> None:
    md = report.to_markdown("협업툴 검토", [{"heading": "비용", "content": DISCOUNT_SECTION}])
    assert "```" not in md
    assert "> **비교 전제**" in md
    assert "> 두 안 모두 40명 기준 월 단가로 산정하며" in md
    assert "- **3,840,000원** — A안 연간 비용(첫해·이듬해 동일)" in md
    assert "- **60.0%** — 파일 버전 혼선 불만 비율(21명/35명)" in md
    assert "## 대안 비교" in md and md.rstrip().endswith("추천 안은 A안입니다.")


def test_steps_cards_and_charts_are_written_as_lists_and_tables() -> None:
    text = (
        "```steps\n자료 수집 | 공개 데이터를 모은다\n분석 | 표로 정리한다\n```\n"
        "```Cards\n## 장점\n- 빠르다\n## 단점\n- 비싸다\n```\n"
        "```chart\nbar | 점\n대학 | 교과내용 | 시설\n공과대학 | 3.66 | 3.69\n"
        "예술대학 | 3.22 | 2.89\n```"
    )
    out = block_fences.plain(text)
    assert "```" not in out
    assert "1. **자료 수집** — 공개 데이터를 모은다" in out and "2. **분석** — 표로 정리한다" in out
    assert "**장점**" in out and "- 비싸다" in out
    assert "| 대학 | 교과내용 | 시설 |" in out and "| 예술대학 | 3.22 | 2.89 |" in out
    assert "(단위: 점)" in out


def test_a_json_chart_is_a_table_and_an_unreadable_one_is_dropped() -> None:
    chart = {"categories": ["공과대학", "예술대학"],
             "series": [{"name": "교과내용", "points": [{"x": "공과대학", "y": 3.66},
                                                       {"x": "예술대학", "y": 3.22}]}]}
    out = block_fences.plain("앞\n\n```chart\n" + json.dumps(chart, ensure_ascii=False) + "\n```")
    assert "| 항목 | 교과내용 |" in out and "| 예술대학 | 3.22 |" in out and "{" not in out
    assert block_fences.plain("앞\n```chart\n그림 자리\n```\n뒤") == "앞\n뒤"


def test_other_fences_are_left_as_written() -> None:
    text = "```python\nprint('```kpi')\n```\n\n```mermaid\nflowchart LR\nA-->B\n```"
    assert block_fences.plain(text) == text


def test_the_bot_api_answer_carries_no_block_fence() -> None:
    answer, cited = bot_api.citations_of(
        "요약입니다.\n\n```kpi\n42% | 응답률\n```\n\n### Sources\n- [1] [보고서](https://a.example/r)")
    assert answer == "요약입니다.\n\n- **42%** — 응답률"
    assert cited == [{"index": 1, "source": "보고서", "url": "https://a.example/r"}]


# ── 2. a tool call written as the answer (second turn) ───────────────────────

COUNSELLOR_SECOND_TURN_CALL = (
    " <tool_call>web_search<arg_key>query</arg_key><arg_value>정신건강복지센터 "
    "정신건강상담 무료 예약 방법</arg_value></tool_call>")


@pytest.mark.parametrize(("written", "call"), [
    (COUNSELLOR_SECOND_TURN_CALL,
     ("web_search", {"query": "정신건강복지센터 정신건강상담 무료 예약 방법"})),
    ('<tool_call>\n{"name": "web_search", "arguments": {"query": "x"}}\n</tool_call>',
     ("web_search", {"query": "x"})),
    ('<|tool_call|>[{"name":"fetch_url","arguments":{"url":"https://a.example"}}]',
     ("fetch_url", {"url": "https://a.example"})),
    ('```json\n{"name": "web_search", "arguments": {"query": "y"}}\n```',
     ("web_search", {"query": "y"})),
    ("<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>web_search\n```json\n"
     '{"query": "z"}\n```<｜tool▁call▁end｜><｜tool▁calls▁end｜>', ("web_search", {"query": "z"})),
    ('<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>web_search<｜tool▁sep｜>{"query": "w"}'
     "<｜tool▁call▁end｜><｜tool▁calls▁end｜>", ("web_search", {"query": "w"})),
    ("<tool_call>\n<function=web_search>\n<parameter=query>\nq1\n</parameter>\n</function>\n"
     "</tool_call>", ("web_search", {"query": "q1"})),
])
def test_a_written_call_is_read_as_the_call_it_meant(written: str, call: tuple) -> None:
    kept, calls = internal_words.written_tool_calls(written, {"web_search", "fetch_url"})
    assert kept == "" and calls == [call]
    assert internal_words.strip_tool_calls(written) == ""


def test_a_call_to_no_tool_is_dropped_and_prose_and_code_stay() -> None:
    kept, calls = internal_words.written_tool_calls(
        "찾아볼게요.\n<tool_call>unknown<arg_key>a</arg_key><arg_value>1</arg_value></tool_call>",
        {"web_search"})
    assert (kept, calls) == ("찾아볼게요.", [])
    # a call format shown on purpose, in code, is the answer
    shown = "Qwen은 `<tool_call>{\"name\": ...}</tool_call>` 형식을 씁니다."
    assert internal_words.written_tool_calls(shown, {"web_search"}) == (shown, [])
    assert internal_words.strip_tool_calls(shown) == shown


class _Response:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _said(text: str) -> list[str]:
    return [f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}", "data: [DONE]"]


_CALL = ('data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
         '"function":{"name":"lookup","arguments":"{}"}}]}}]}')
_EMPTY = ['data: {"choices":[{"delta":{},"finish_reason":"stop"}]}', "data: [DONE]"]


async def _turn(monkeypatch, replies: list[list[str]], request: str, tool_name: str = "lookup"):
    seen: list[dict] = []
    ran: list[dict] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            seen.append(json)
            return _Response(replies[min(len(seen), len(replies)) - 1])

    async def client(*_args, **_kwargs):
        return Client()

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(args):
        ran.append(args)
        return ToolResult(content="정신건강복지센터는 무료 상담을 하며 전화로 예약한다.")

    tool = Tool(name=tool_name, description="찾는다", parameters={"type": "object"}, run=lookup,
                label="찾기")
    events = [
        event
        async for event in agent.run_turn(
            "local/qwen3.8-flash",
            [{"role": "system", "content": "시스템"}, {"role": "user", "content": request}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key", request=request),
        )
    ]
    return events, seen, ran


def _shown(events: list[dict]) -> str:
    """What the reader is left with after retracts (first match taken back, as the client)."""
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract" and event["text"] in text:
            at = text.index(event["text"])
            text = text[:at] + text[at + len(event["text"]):]
    return text


@pytest.mark.asyncio
async def test_a_written_call_to_an_offered_tool_is_run(monkeypatch) -> None:
    written = ("<tool_call>lookup<arg_key>query</arg_key><arg_value>상담 예약</arg_value>"
               "</tool_call>")
    answer = "가까운 정신건강복지센터에 전화로 예약하면 무료 상담을 받을 수 있습니다."
    events, seen, ran = await _turn(monkeypatch, [_said(written), _said(answer)],
                                    "상담은 받아 본 적 없어.")
    assert ran == [{"query": "상담 예약"}]
    assert _shown(events) == answer
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_a_reply_that_is_only_a_call_to_no_tool_is_written_again(monkeypatch) -> None:
    written = COUNSELLOR_SECOND_TURN_CALL
    answer = "몇 달째 이어진 무기력은 전문 상담을 받아 볼 신호입니다."
    events, seen, ran = await _turn(monkeypatch, [_said(written), _said(answer)],
                                    "몇 달째 아침에 일어나기가 힘들어.")
    assert ran == []
    assert _shown(events) == answer and "<tool_call>" not in _shown(events)
    # the second write has no tools and is told not to write a call
    assert "tools" not in seen[-1]


# ── 3. a mark never inside a number (「3.5(확인 필요).0」) ────────────────────

_EV = value_guard.Evidence.of(["변경 목록: 한빛워크 3.4.1 수정 버전. 퇴사자 일괄 비활성화 추가."])


@pytest.mark.parametrize(("before", "blanked", "marked"), [
    ("기능이 추가된 배포라면 3.5.0으로 올리는 것이 맞는지 판단해 주세요.",
     "기능이 추가된 배포라면 [버전]으로 올리는 것이 맞는지 판단해 주세요.",
     "기능이 추가된 배포라면 3.5.0(확인 필요)으로 올리는 것이 맞는지 판단해 주세요."),
    ("합계는 1,234.5원입니다.", "합계는 [수치]원입니다.", "합계는 1,234.5원(확인 필요)입니다."),
    ("비율은 3.5%입니다.", "비율은 [수치]입니다.", "비율은 3.5%(확인 필요)입니다."),
    ("마감은 10/15(목)입니다.", "마감은 [날짜]입니다.", "마감은 10/15(목)(확인 필요)입니다."),
    ("종료일은 2026.12.31입니다.", "종료일은 [날짜]입니다.",
     "종료일은 2026.12.31(확인 필요)입니다."),
])
def test_the_mark_goes_after_the_whole_value(before: str, blanked: str, marked: str) -> None:
    assert value_guard.settle(before, blanked, _EV).text == marked


def test_token_end_moves_past_the_rest_of_the_value() -> None:
    line = "3.5.0으로 1,234.5원 3.5% 10/15(목) 2026.12.31 끝"
    assert line[:value_guard.token_end(line, 3)] == "3.5.0"
    at = line.index("1,234")
    assert line[at:value_guard.token_end(line, at + 5)] == "1,234.5원"
    at = line.index("3.5%")
    assert line[at:value_guard.token_end(line, at + 3)] == "3.5%"
    at = line.index("10/15")
    assert line[at:value_guard.token_end(line, at + 2)] == "10/15(목)"
    at = line.index("2026.12")
    assert line[at:value_guard.token_end(line, at + 4)] == "2026.12.31"
    # not inside a number: as it is
    assert value_guard.token_end("끝.", 1) == 1


def test_the_copy_check_marks_after_the_whole_value() -> None:
    text = "이번 버전은 3.5.0입니다."
    found = [{"line": 0, "end": 10, "text": "이번 버전은 3.5", "action": "drop"}]
    assert copy_check.mark_left(text, found) == "이번 버전은 3.5.0 (확인 필요)입니다."


# ── 4. the answer's language (an English question answered in Korean) ──────────────

HISTORIAN_REQUEST = (
    "I want you to act as a historian. You will research and analyze cultural, economic, "
    "political, and social events in the past. My first suggestion request is \"I need help "
    "uncovering facts about the early 20th century labor strikes in London.\"")


def test_the_loops_notes_keep_an_english_answer_english() -> None:
    system = [{"role": "system", "content": "You are KloudChat's assistant."}]
    assert not agent._writes_korean(HISTORIAN_REQUEST, system)
    assert agent._writes_korean("런던 파업을 정리해 줘", system)
    # the turn's own ask wins; then the agent's set language
    assert not agent._writes_korean("런던 파업을 영어로 정리해 줘", system)
    set_ko = [{"role": "system", "content": output_language.rule("ko")}]
    assert agent._writes_korean(HISTORIAN_REQUEST, set_ko)
    assert agent._answer_anomaly("same " * 20, [
        {"role": "assistant", "content": "same " * 20}], korean=False)[1] == agent._ECHO_EN


def _searched(n: int) -> list[str]:
    call = {"index": 0, "id": f"s{n}",
            "function": {"name": "web_search", "arguments": json.dumps({"query": f"q{n}"})}}
    return [f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [call]}}]})}",
            "data: [DONE]"]


def _notes(seen: list[dict]) -> list[str]:
    """The notes the loop added as user turns (the request itself left out)."""
    return list(dict.fromkeys(
        str(m["content"]) for s in seen for m in s["messages"][2:] if m["role"] == "user"))


@pytest.mark.asyncio
async def test_the_search_cap_note_is_english_in_an_english_conversation(monkeypatch) -> None:
    replies = [_searched(1), _searched(2), _searched(3), _said("The Great Unrest ran 1910-14.")]
    _, seen, _ = await _turn(monkeypatch, replies, HISTORIAN_REQUEST, tool_name="web_search")
    notes = _notes(seen)
    assert notes == [agent._SEARCH_CAP_EN.format(done="You have searched the web enough.")]
    assert not any(re.search(r"[가-힣]", n) for n in notes)


@pytest.mark.asyncio
async def test_a_korean_conversation_keeps_korean_notes(monkeypatch) -> None:
    replies = [_searched(1), _searched(2), _searched(3), _said("1910~1914년 대불안 시기입니다.")]
    _, seen, _ = await _turn(monkeypatch, replies, "20세기 초 런던 파업을 정리해 줘",
                             tool_name="web_search")
    notes = _notes(seen)
    assert len(notes) == 1 and notes[0].startswith("웹 검색은 충분히 했습니다.")


@pytest.mark.asyncio
async def test_a_note_after_an_empty_reply_is_in_the_question_language(monkeypatch) -> None:
    answer = "The 1911 dock strike began in August."
    _, seen, _ = await _turn(monkeypatch, [[_CALL, "data: [DONE]"], _EMPTY, _said(answer)],
                             HISTORIAN_REQUEST)
    assert agent._EMPTY_EN in _notes(seen)
    assert not any(re.search(r"[가-힣]", n) for n in _notes(seen))


def test_an_english_answer_gets_english_marks() -> None:
    english = ("The strike wave of 1910–1914 is known as the Great Unrest. About 4,000 dockers "
               "(확인 필요) stopped work in Liverpool in 1911.")
    assert output_language.localize_marks(english) == english.replace(
        "(확인 필요)", "(needs checking)")
    korean = "1911년 리버풀에서 부두 노동자 약 4,000명(확인 필요)이 파업했습니다."
    assert output_language.localize_marks(korean) == korean


def test_english_copy_gets_its_check_note_in_english() -> None:
    copy = "Join the webinar on November 27 and see the new console live (확인 필요)."
    out = copy_check.lift_marks(copy)
    assert "확인" not in out and "Check before sending:" in out


def test_the_chat_finish_runs_the_mark_language_before_the_swap() -> None:
    flow = registry.pipeline("chat").flow("finish")
    assert flow.index("copy-marks") < flow.index("answer-marks") < flow.index("answer-replace")
    assert registry.stage("answer-marks").skip(chat_stages.bundle_for(
        answer="1911년 파업(확인 필요)이 있었습니다.", request="", model={"id": "m"},
        actual_model=None, api_key="", session_id="s", today=None, first_message="",
        messages=[], material="", check_keys=(), check_material="", tool_outputs=[], judge="",
        kept_steps=[], usage={}))
