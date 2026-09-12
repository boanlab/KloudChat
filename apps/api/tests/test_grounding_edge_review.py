"""Adversarial current-fact boundaries found while reviewing the grounding patch."""

from __future__ import annotations

import pytest

from app.services import agent, freshness
from app.services.tools.base import Tool, ToolContext, ToolResult


@pytest.mark.parametrize(
    "question",
    [
        "현재 대통령이 누구인지 알려주고 역할은 설명하지 마",
        "현재 대표이사가 누구인지 알려주고 역할은 설명하지 마",
        "최신 환율과 환율의 정의 알려줘",
    ],
)
def test_explicit_current_fact_is_not_erased_by_a_role_or_definition_subrequest(question):
    assert freshness.current_fact_required(question)


@pytest.mark.asyncio
async def test_current_fact_closing_hop_with_invented_tool_call_does_not_finish_empty(monkeypatch):
    model_calls = []
    tool_calls = []

    async def stream(_model, _messages, tools, *_args, **_kwargs):
        hop = len(model_calls)
        model_calls.append([tool.name for tool in tools])
        acc = agent._Accumulator()
        acc.content = ["UNSUPPORTED_PRETOOL_GUESS"]
        acc.calls = {
            0: {"id": f"s{hop}", "name": "read_value", "arguments": f'{{"attempt":{hop}}}'}
        }
        acc.usage = {"inputTokens": 1, "outputTokens": 1}
        yield "delta", acc.content[0]
        yield "done", acc

    async def read_value(arguments):
        tool_calls.append(arguments)
        return ToolResult(content="No current value is available")

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 1)
    tool = Tool(
        name="read_value",
        description="Synthetic fixture",
        parameters={"type": "object"},
        run=read_value,
        label="Read",
    )
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/local",
            [{"role": "user", "content": "Current product price"}],
            [tool],
            ToolContext(user_id="synthetic", session_id="synthetic"),
            freshness_request="Current product price",
        )
    ]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert len(tool_calls) == 1
    assert model_calls[-1] == []
    assert "UNSUPPORTED_PRETOOL_GUESS" not in text
    assert text.strip(), "A completed turn must explain its limit instead of saving an empty answer"
    assert events[-1] == {"type": "usage", "inputTokens": 3, "outputTokens": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["empty", "looped", "runaway"])
async def test_current_fact_incomplete_completion_is_visible_without_hidden_draft(
    monkeypatch, outcome
):
    async def stream(*_args, **_kwargs):
        acc = agent._Accumulator()
        if outcome != "empty":
            acc.content = ["UNSUPPORTED_HIDDEN_DRAFT" + "a" * 40]
            yield "delta", acc.content[0]
        acc.looped = outcome == "looped"
        acc.runaway = "a" * 40 if outcome == "runaway" else None
        acc.usage = {"inputTokens": 1, "outputTokens": 1}
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/local",
            [{"role": "user", "content": "Current product price"}],
            [],
            ToolContext(user_id="synthetic", session_id="synthetic"),
            freshness_request="Current product price",
        )
    ]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert text.strip()
    assert "UNSUPPORTED_HIDDEN_DRAFT" not in text
    assert not any(event["type"] == "retract" and not event["text"] for event in events)
    assert events[-1] == {"type": "usage", "inputTokens": 1, "outputTokens": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["", "SYNTHETIC_PRIVATE_VALUE"])
async def test_current_fact_terminal_result_is_nonempty_and_masked_without_a_model_call(
    monkeypatch, terminal
):
    async def stream(*_args, **_kwargs):
        raise AssertionError("Terminal result must not make another model call")
        yield  # pragma: no cover

    async def read_value(_arguments):
        return ToolResult(content="Synthetic result", final_text=terminal)

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 1)
    tool = Tool(
        name="read_value",
        description="Synthetic fixture",
        parameters={"type": "object"},
        run=read_value,
        label="Read",
    )
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/local",
            [{"role": "user", "content": "Current product price"}],
            [tool],
            ToolContext(user_id="synthetic", session_id="synthetic"),
            freshness_request="Current product price",
            preset_call=("read_value", {}),
            sanitize_tool_output=lambda text: (
                text.replace("SYNTHETIC_PRIVATE_VALUE", "[MASKED]"),
                text.count("SYNTHETIC_PRIVATE_VALUE"),
            ),
        )
    ]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert text.strip()
    assert "SYNTHETIC_PRIVATE_VALUE" not in text
    if terminal:
        # A terminal result from an unapproved reader cannot become current evidence.
        assert text == "The current state was not verified in this request."
        assert any(event["type"] == "privacy_route" for event in events)
    assert events[-1] == {"type": "usage", "inputTokens": 0, "outputTokens": 0}
