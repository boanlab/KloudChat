"""Required verification offers only its schema, without expanding permissions."""

from copy import deepcopy

import pytest

from app.services import agent
from app.services.tools.arithmetic import CALCULATE
from app.services.tools.base import Tool, ToolContext, ToolResult, to_openai
from app.services.tools.ncs_check import CHECK_NCS_ANSWER


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [False, True])
async def test_only_required_schema_is_offered_until_verification_succeeds(monkeypatch, snapshot):
    calls = []

    async def forbidden(_arguments):
        pytest.fail("unrelated tool ran during verification")

    other = Tool(
        name="unrelated",
        description="unrelated",
        parameters={"type": "object"},
        run=forbidden,
        label="other",
        read_only=False,
    )
    tools = [other, CALCULATE]
    definitions = to_openai(tools) if snapshot else None
    original = deepcopy(definitions)

    async def stream(_model, _messages, offered, *_args, **kwargs):
        calls.append(([tool.name for tool in offered], deepcopy(kwargs)))
        acc = agent._Accumulator()
        if len(calls) == 1:
            # Failed arithmetic is retried with the same restricted schema.
            acc.calls[0] = {"id": "bad", "name": "calculate", "arguments": '{"expression":"1/0"}'}
        elif len(calls) == 2:
            acc.calls[0] = {
                "id": "good",
                "name": "calculate",
                "arguments": '{"expression":"17*23"}',
            }
        else:
            acc.content.append("391")
            yield "delta", "391"
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 3)
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/model",
            [],
            tools,
            ToolContext(user_id="qa", session_id="qa"),
            preflight_tool="calculate",
            tool_definitions=definitions,
        )
    ]
    assert [names for names, _ in calls] == [
        ["calculate"],
        ["calculate"],
        ["unrelated", "calculate"],
    ]
    if snapshot:
        assert [
            [row["function"]["name"] for row in kwargs["tool_definitions"]] for _, kwargs in calls
        ] == [
            ["calculate"],
            ["calculate"],
            ["unrelated", "calculate"],
        ]
    assert definitions == original
    assert [tool.name for tool in tools] == ["unrelated", "calculate"]
    assert "".join(event["text"] for event in events if event["type"] == "delta") == "391"
    assert [event["status"] for event in events if event["type"] == "step"] == [
        "running",
        "error",
        "running",
        "done",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["not_applicable", "needs_input"])
async def test_required_arithmetic_cannot_be_satisfied_by_skipping_the_calculation(
    monkeypatch, decision
):
    model_calls = []

    async def stream(*_args, **_kwargs):
        model_calls.append(True)
        assert len(model_calls) == 1, "a non-calculation decision unlocked an answer hop"
        acc = agent._Accumulator()
        acc.calls[0] = {
            "id": "skip",
            "name": "check_ncs_answer",
            "arguments": '{"decision":"' + decision + '","reason":"synthetic decision"}',
        }
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/model",
            [],
            [CHECK_NCS_ANSWER],
            ToolContext(user_id="qa", session_id="qa"),
            preflight_tool="check_ncs_answer",
            calculation_required=True,
        )
    ]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert "확정할 수 없습니다" in text
    assert "synthetic decision" not in text
    assert [event["status"] for event in events if event["type"] == "step"] == ["running", "error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_state", ["ok", "failed", "empty"])
async def test_trusted_source_lookup_precedes_required_calculation_once(monkeypatch, source_state):
    order = []

    async def lookup(_arguments):
        order.append("search")
        return ToolResult(
            content="Synthetic item price is 17.",
            failed=source_state == "failed",
            empty=source_state == "empty",
        )

    search = Tool(
        name="web_search",
        description="synthetic lookup",
        parameters={"type": "object"},
        run=lookup,
        label="search",
    )

    async def stream(_model, messages, offered, *_args, **kwargs):
        assert source_state == "ok", "unsuccessful prerequisite reached the model"
        order.append("model")
        acc = agent._Accumulator()
        if order.count("model") == 1:
            assert order[0] == "search"
            assert [tool.name for tool in offered] == ["calculate"]
            assert any(message.get("name") == "web_search" for message in messages)
            acc.calls[0] = {
                "id": "calc",
                "name": "calculate",
                "arguments": '{"expression":"17*23"}',
            }
        else:
            assert "force_tool" not in kwargs
            acc.content.append("391")
            yield "delta", "391"
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/model",
            [],
            [search, CALCULATE],
            ToolContext(user_id="qa", session_id="qa"),
            preflight_tool="calculate",
            calculation_required=True,
            force_tool="web_search",
            preset_call=("web_search", {"query": "synthetic item price"}),
        )
    ]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    if source_state == "ok":
        assert order == ["search", "model", "model"]
        assert text == "391"
        assert sum(event.get("label") == "수치 계산" for event in events) == 1
    else:
        assert order == ["search"]
        assert "확정할 수 없습니다" in text
        assert "391" not in text


@pytest.mark.asyncio
async def test_repeated_successful_calculation_does_not_revoke_completed_verification(monkeypatch):
    seen = []

    async def stream(_model, messages, *_args, **_kwargs):
        seen.append(deepcopy(messages))
        acc = agent._Accumulator()
        if len(seen) <= 2:
            acc.calls[0] = {
                "id": str(len(seen)),
                "name": "calculate",
                "arguments": '{"expression":"17*23"}',
            }
        else:
            acc.content.append("391")
            yield "delta", "391"
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/model",
            [],
            [CALCULATE],
            ToolContext(user_id="qa", session_id="qa"),
            preflight_tool="calculate",
            calculation_required=True,
        )
    ]
    assert len(seen) == 3
    assert "".join(event["text"] for event in events if event["type"] == "delta") == "391"
    assert not any(event.get("status") == "error" for event in events)
    assert "이미 호출했습니다" in seen[-1][-1]["content"]
