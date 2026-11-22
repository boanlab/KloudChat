"""A refused write may be sent once more exactly as it was; a third identical call is refused; a
read is never repeated."""

from __future__ import annotations

import pytest

from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult


def _model(monkeypatch, calls):
    hops = []

    async def completion(_model, messages, tools, *_args, **kwargs):
        hops.append(messages)
        acc = agent._Accumulator()
        if len(hops) <= calls:
            acc.calls[0] = {"id": f"c{len(hops)}", "name": "doc_write",
                            "arguments": '{"slides": 13}'}
        else:
            acc.content = ["끝났습니다."]
        acc.usage = {"inputTokens": 3, "outputTokens": 5}
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", completion)
    return hops


@pytest.mark.asyncio
async def test_the_same_refused_write_runs_twice_then_is_a_repeat(monkeypatch):
    runs = []

    async def write(args, ctx):
        runs.append(args)
        return ToolResult(content="요청은 12장입니다.", failed=True)

    tool = Tool(name="doc_write", description="d", parameters={"type": "object"}, run=write,
                label="쓰는 중", read_only=False, wants_context=True)
    _model(monkeypatch, 3)
    ctx = ToolContext(user_id="u", session_id="s")
    events = [e async for e in agent.run_turn(
        "m", [{"role": "user", "content": "12장"}], [tool], ctx)]
    assert len(runs) == 2
    assert events
