"""도구 호출 한도에 닿아도 답은 나온다.

A model that is still calling tools has written no prose yet, so stopping at
the cap used to leave the person with one line — 「도구 호출이 5회를 넘어
중단했습니다」 — and none of what five searches had found. The last hop now
runs without tools and is told to answer from what it has.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult


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


class _Client:
    """Calls a tool on every hop until it is given none, then answers."""

    def __init__(self, seen: list[dict]) -> None:
        self._seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        if json.get("tools"):
            return _Response(
                [
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                    '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                    "data: [DONE]",
                ]
            )
        return _Response(
            [
                'data: {"choices":[{"delta":{"content":"모은 자료로 쓴 답"}}]}',
                "data: [DONE]",
            ]
        )


@pytest.mark.asyncio
async def test_the_cap_ends_in_an_answer_written_without_tools(monkeypatch) -> None:
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen)

    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(settings, "max_tool_hops", 2)

    async def lookup(_args, _ctx):
        return ToolResult(content="찾은 것")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "확인해 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]

    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "모은 자료로 쓴 답" in text
    assert "중단했습니다" not in text
    # The closing call carried no tools and the instruction to answer.
    assert "tools" not in seen[-1]
    assert any("도구는 더 쓸 수 없습니다" in (m.get("content") or "") for m in seen[-1]["messages"])
