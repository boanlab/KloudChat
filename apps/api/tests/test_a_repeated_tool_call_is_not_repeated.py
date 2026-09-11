"""A model that cannot tell it already has an answer keeps calling the same
tool with the same arguments hop after hop; the loop must not pay for the
same call twice, and must tell the model so."""

from __future__ import annotations

import json

import pytest

from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult


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
    def __init__(self, seen: list[dict], scripted: list[list[str]]) -> None:
        self._seen = seen
        self._scripted = scripted

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        return _Response(self._scripted.pop(0))


def _content(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


def _calls(name: str, arguments: dict, call_id: str = "c0") -> list[str]:
    return [
        "data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": call_id,
                                    "function": {"name": name, "arguments": json.dumps(arguments)},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]


@pytest.mark.asyncio
async def test_the_same_call_twice_only_runs_the_tool_once(monkeypatch) -> None:
    runs = 0

    async def run_weather(_args):
        nonlocal runs
        runs += 1
        return ToolResult(content="맑음, 25도", detail="후쿠오카")

    tools = [
        Tool(
            name="weather",
            description="날씨",
            parameters={"type": "object"},
            run=run_weather,
            label="날씨",
        )
    ]
    seen: list[dict] = []
    scripted = [
        _calls("weather", {"location": "후쿠오카"}, "c0"),
        _calls("weather", {"location": "후쿠오카"}, "c1"),
        [_content("맑고 따뜻합니다."), "data: [DONE]"],
    ]

    async def client(*_args, **_kwargs):
        return _Client(seen, scripted)

    monkeypatch.setattr(agent, "_client", client)
    ctx = ToolContext(user_id="user", session_id="session", api_key="key")
    async for _ in agent.run_turn("m", [{"role": "user", "content": "q"}], tools, ctx):
        pass

    assert runs == 1
    # The second call's tool message tells the model why, instead of the real result.
    tool_messages = [m for m in seen[2]["messages"] if m.get("role") == "tool"]
    assert tool_messages[0]["content"] == "맑음, 25도"
    assert "이미 호출했습니다" in tool_messages[1]["content"]


@pytest.mark.asyncio
async def test_different_arguments_both_run(monkeypatch) -> None:
    runs: list[str] = []

    async def run_weather(args):
        runs.append(args["location"])
        return ToolResult(content="맑음", detail=args["location"])

    tools = [
        Tool(
            name="weather",
            description="날씨",
            parameters={"type": "object"},
            run=run_weather,
            label="날씨",
        )
    ]

    scripted = [
        _calls("weather", {"location": "서울"}, "c0"),
        _calls("weather", {"location": "부산"}, "c1"),
        [_content("서울도 부산도 맑습니다."), "data: [DONE]"],
    ]

    async def client(*_args, **_kwargs):
        return _Client([], scripted)

    monkeypatch.setattr(agent, "_client", client)
    ctx = ToolContext(user_id="user", session_id="session", api_key="key")
    async for _ in agent.run_turn("m", [{"role": "user", "content": "q"}], tools, ctx):
        pass

    assert runs == ["서울", "부산"]


@pytest.mark.asyncio
async def test_only_calls_that_ran_are_counted_on_the_context(monkeypatch) -> None:
    """The usage ledger reads `ctx.tool_calls`: a refused repeat is not a search."""

    async def run_search(_args):
        return ToolResult(content="결과", detail="q")

    tools = [
        Tool(
            name="web_search",
            description="검색",
            parameters={"type": "object"},
            run=run_search,
            label="웹 검색",
        )
    ]
    scripted = [
        _calls("web_search", {"query": "a"}, "c0"),
        _calls("web_search", {"query": "a"}, "c1"),
        _calls("web_search", {"query": "b"}, "c2"),
        [_content("답."), "data: [DONE]"],
    ]

    async def client(*_args, **_kwargs):
        return _Client([], scripted)

    monkeypatch.setattr(agent, "_client", client)
    ctx = ToolContext(user_id="user", session_id="session", api_key="key")
    async for _ in agent.run_turn("m", [{"role": "user", "content": "q"}], tools, ctx):
        pass

    assert ctx.tool_calls == {"web_search": 2}
