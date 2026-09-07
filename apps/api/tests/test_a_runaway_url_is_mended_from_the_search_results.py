"""A stream whose URL trails off into 「000000…」 is stopped, the run taken back,
and the address completed from the one the search tool returned."""

from __future__ import annotations

import json

import pytest

from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult

_KNOWN = "https://www.dt.co.kr/contents.html?article_no=2026011802109931731004"


class _Response:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _Client:
    def __init__(self, scripted: list[list[str]]) -> None:
        self._scripted = scripted

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:  # noqa: ARG002
        return _Response(self._scripted.pop(0))


def _content(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


async def _run_search(_args):
    return ToolResult(content=f"[1] 기사 제목\n{_KNOWN}\n요약문")


_SEARCH = Tool(
    name="web_search",
    description="웹 검색",
    parameters={"type": "object", "properties": {}},
    run=_run_search,
    label="웹 검색 중",
)

_CALLS_SEARCH = [
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c0",'
    '"function":{"name":"web_search","arguments":"{}"}}]}}]}',
    "data: [DONE]",
]


def _runaway_answer() -> list[str]:
    lines = [
        _content(
            "기사는 여기 있습니다. [디지털타임스](https://www.dt.co.kr/contents.html?article_no=20260118020"
        )
    ]
    lines += [_content("00")] * 120
    lines += [_content(") 끝."), "data: [DONE]"]
    return lines


def _ctx() -> ToolContext:
    return ToolContext(user_id="user", session_id="session", api_key="key")


async def _collect(monkeypatch, scripted: list[list[str]]):
    async def client(*_args, **_kwargs):
        return _Client(scripted)

    monkeypatch.setattr(agent, "_client", client)
    events = []
    async for event in agent.run_turn("m", [{"role": "user", "content": "q"}], [_SEARCH], _ctx()):
        events.append(event)
    return events


def _final_text(events: list[dict]) -> str:
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            text = text.replace(event["text"], "", 1)
    return text


@pytest.mark.asyncio
async def test_the_run_is_taken_back_and_the_url_completed(monkeypatch) -> None:
    events = await _collect(monkeypatch, [_CALLS_SEARCH, _runaway_answer()])
    text = _final_text(events)
    assert f"[디지털타임스]({_KNOWN})" in text
    assert "0000000000" not in text
    assert "바로잡았습니다" in text
    # The stream was closed at the run, so the text after it never arrived.
    assert "끝." not in text


@pytest.mark.asyncio
async def test_the_stream_stops_early(monkeypatch) -> None:
    events = await _collect(monkeypatch, [_CALLS_SEARCH, _runaway_answer()])
    zero_deltas = [e for e in events if e["type"] == "delta" and e["text"] == "00"]
    assert 0 < len(zero_deltas) < 40


@pytest.mark.asyncio
async def test_a_run_outside_any_url_is_only_cut(monkeypatch) -> None:
    answer = [_content("결과는 "), *([_content("aa")] * 60), "data: [DONE]"]
    events = await _collect(monkeypatch, [answer])
    text = _final_text(events)
    assert "aaaaaaaaaa" not in text
    assert "되풀이되어 여기서 멈췄습니다" in text


def test_the_repair_helper_trims_the_models_own_zeros() -> None:
    seen = {"https://x.test/a?id=2010042"}
    kept, tail, outcome = agent._repair_runaway(
        "본문 [링크](https://x.test/a?id=201000" + "0" * 40, "0" * 40, seen
    )
    # The agreement reaches one of the model's zeros; the rest were the run.
    assert (kept, tail, outcome) == ("본문 [링크](https://x.test/a?id=20100", "42)", "completed")


def test_an_ambiguous_prefix_is_not_guessed() -> None:
    seen = {"https://x.test/a?id=2010042", "https://x.test/a?id=2010099"}
    kept, tail, outcome = agent._repair_runaway(
        "본문 [링크](https://x.test/a?id=2010" + "0" * 40, "0" * 40, seen
    )
    assert (kept, tail, outcome) == ("본문 ", "링크", "dropped")


def test_an_unknown_bare_url_is_dropped_whole() -> None:
    kept, tail, outcome = agent._repair_runaway(
        "출처: https://y.test/" + "9" * 40, "9" * 40, {"https://x.test/"}
    )
    assert (kept, tail, outcome) == ("출처: ", "", "dropped")


def test_a_run_outside_a_url_is_only_cut() -> None:
    kept, tail, outcome = agent._repair_runaway("아" + "a" * 40, "a" * 40, set())
    assert (kept, tail, outcome) == ("아", "", "cut")
