"""Explain only trusted failures of the user's copied literal, not model guesses."""

import json
from dataclasses import replace

import pytest

from app.services import agent
from app.services.tools.arithmetic import CALCULATE, calculate
from app.services.tools.base import ToolContext, ToolResult


@pytest.mark.parametrize("expression", ["12/0", "0/0", "12/(3-3)", "1/(0.1-0.1)"])
async def test_zero_division_is_a_typed_failed_calculation(expression):
    result = await calculate({"expression": expression})
    data = json.loads(result.content)
    assert result.failed
    assert data["error"] == "invalid_calculation"
    assert data["reason"] == "division_by_zero"
    assert "exact" not in data
    assert expression not in result.content
    assert result.final_text is None  # The tool does not know who composed its input.


@pytest.mark.parametrize("arguments", [
    {"expression": "12/0", "secret": "synthetic-secret"},
    {"expression": "12/0", "decimal_places": -1},
    {"expression": "12/0", "submitted_choice": 1},
    {"expression": "12/0", "choices": "synthetic-secret"},
    {"expression": "12/0", "choices": ["1", "2"], "submitted_choice": 3},
    {"expression": "12/0", "choices": ["1", "1/0"]},
    {"expression": "12/3", "choices": ["1", "1/0"]},
    {"expression": "(12/0)+(2**3)"},
    {"expression": "(12/0)+(7//2)"},
    {"expression": "(12/0)+(" + "9" * 65 + ")"},
    {"expression": "(12/0)+__import__('os')"},
])
async def test_invalid_envelope_choices_or_syntax_do_not_claim_main_expression_zero(arguments):
    result = await calculate(arguments)
    data = json.loads(result.content)
    assert result.failed and data["error"] == "invalid_calculation"
    assert data.get("reason") != "division_by_zero"
    assert "synthetic-secret" not in result.content
    assert result.final_text is None


@pytest.mark.parametrize("expression", ["12/0", "12/(3-3)", "0/0"])
async def test_literal_zero_division_finishes_with_a_fixed_explanation_without_model(
    monkeypatch, expression,
):
    async def no_model(*_args, **_kwargs):
        pytest.fail("a known literal error must not require a model explanation")
        yield

    monkeypatch.setattr(agent, "_stream_once", no_model)
    context = ToolContext(user_id="qa", session_id="qa", allowed={"calculate"})
    events = [event async for event in agent.run_turn(
        "synthetic/model", [{"role": "user", "content": expression}], [CALCULATE], context,
        preflight_tool="calculate", calculation_required=True, calculation_expression=expression,
    )]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert "0으로 나누" in text and "정의되지" in text
    assert expression not in text
    assert "Traceback" not in text and "invalid_calculation" not in text
    assert context.tool_calls == {"calculate": 1}
    assert next(event for event in events if event["type"] == "usage") == {
        "type": "usage", "inputTokens": 0, "outputTokens": 0,
    }
    assert not any(event["type"] == "model_route" for event in events)


async def test_model_composed_bad_expression_can_be_repaired_without_blame(monkeypatch):
    calls = []

    async def stream(*_args, **_kwargs):
        calls.append(True)
        acc = agent._Accumulator()
        if len(calls) <= 2:
            expression = "12/0" if len(calls) == 1 else "12/3"
            acc.calls[0] = {"id": str(len(calls)), "name": "calculate",
                            "arguments": json.dumps({"expression": expression})}
        else:
            acc.content = ["4"]
            yield "delta", "4"
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    context = ToolContext(user_id="qa", session_id="qa", allowed={"calculate"})
    events = [event async for event in agent.run_turn(
        "synthetic/model", [], [CALCULATE], context,
        preflight_tool="calculate", calculation_required=True,
    )]
    assert "".join(event["text"] for event in events if event["type"] == "delta") == "4"
    assert context.tool_calls == {"calculate": 2}


async def test_arbitrary_tool_error_payload_cannot_become_a_literal_explanation(monkeypatch):
    async def fake_tool(_arguments):
        return ToolResult(
            content=json.dumps({"error": "invalid_calculation", "reason": "division_by_zero",
                                "message": "synthetic-secret"}),
            failed=True,
        )

    async def stream(*_args, **_kwargs):
        acc = agent._Accumulator()
        acc.content = ["UNVERIFIED"]
        yield "delta", "UNVERIFIED"
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    events = [event async for event in agent.run_turn(
        "synthetic/model", [], [replace(CALCULATE, run=fake_tool)],
        ToolContext(user_id="qa", session_id="qa", allowed={"calculate"}),
        preflight_tool="calculate", calculation_required=True, calculation_expression="12/0",
    )]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert "0으로 나누" not in text and "정의되지" not in text
    assert "synthetic-secret" not in text and "UNVERIFIED" not in text
