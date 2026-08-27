"""A tool has a name and a progress label, and they are different words.

`Tool.label` was documented as the in-flight string ("searching the web") and
then used as the tool's name too: the permission list read 웹 검색 중 under
every chip, and a finished step kept the same words beside its check mark
while the header above said 작업 완료.
"""

from __future__ import annotations

import pytest

from app.services import agent
from app.services.agent import ToolContext
from app.services.chat import step_label, step_title
from app.services.tools import builtin
from app.services.tools.base import Tool, ToolResult

_BUILTINS = (
    builtin.WEB_SEARCH,
    builtin.FETCH_URL,
    builtin.EXECUTE_CODE,
    builtin.CREATE_ARTIFACT,
    builtin.CREATE_CHART,
    builtin.SHARE_NOTE,
)


def test_every_builtin_has_a_noun_and_a_progress_label() -> None:
    for tool in _BUILTINS:
        assert tool.title, tool.name
        assert not tool.title.endswith("중"), (tool.name, tool.title)
        assert tool.label.endswith("중"), (tool.name, tool.label)
        assert tool.title != tool.label


def test_the_fallback_maps_agree_on_which_is_which() -> None:
    assert step_label("web_search") == "웹 검색 중"
    assert step_title("web_search") == "웹 검색"
    assert step_title("connector__deep_research") == "심층 조사"
    # Unknown tools stay honest in both forms.
    assert step_title("frobnicate_widget") == "frobnicate widget"


@pytest.mark.asyncio
async def test_a_finished_step_is_named_and_a_running_one_is_in_progress(monkeypatch) -> None:
    async def runner(_arguments):
        return ToolResult(content="ok")

    tool = Tool(
        name="lookup",
        description="lookup",
        parameters={"type": "object", "properties": {}},
        run=runner,
        label="찾는 중",
        title="찾기",
    )
    hops = 0

    async def fake_stream_once(*_args, **_kwargs):
        nonlocal hops
        hops += 1
        acc = agent._Accumulator()
        if hops == 1:
            acc.calls[0] = {"id": "call_0", "name": "lookup", "arguments": "{}"}
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", fake_stream_once)

    steps = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
        if event["type"] == "step"
    ]

    assert [(s["status"], s["label"]) for s in steps] == [("running", "찾는 중"), ("done", "찾기")]
    assert len({s["id"] for s in steps}) == 1


@pytest.mark.asyncio
async def test_a_tool_without_a_title_still_reads_as_before(monkeypatch) -> None:
    async def runner(_arguments):
        return ToolResult(content="ok")

    tool = Tool(
        name="legacy",
        description="legacy",
        parameters={"type": "object", "properties": {}},
        run=runner,
        label="legacy",
    )
    hops = 0

    async def fake_stream_once(*_args, **_kwargs):
        nonlocal hops
        hops += 1
        acc = agent._Accumulator()
        if hops == 1:
            acc.calls[0] = {"id": "call_0", "name": "legacy", "arguments": "{}"}
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", fake_stream_once)
    labels = [
        e["label"]
        async for e in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
        if e["type"] == "step"
    ]
    assert labels == ["legacy", "legacy"]
