"""Current operand evidence and arithmetic verification are independent gates."""

import pytest
from test_calculation_read_prerequisites import _call
from test_current_evidence import _mock_model, _tool, _visible
from test_plain_chat_tools import _routed_turn

from app.services import agent, freshness
from app.services.tools.arithmetic import CALCULATE
from app.services.tools.base import SearchEvidence, ToolContext, ToolResult

QUESTION = "현재 환율로 100달러와 20달러의 합계 원화 금액을 계산해줘."
GUESSED_ANSWER = "현재 환율은 1달러에 1300원이며 총액은 156000원입니다."


async def _execute(monkeypatch, plans, *, tools, captured=None, preset_call=None):
    snapshots = []
    _mock_model(monkeypatch, plans, snapshots)
    captured = captured or {}
    current_request = captured.get("freshness_request", QUESTION)
    messages = captured.get("messages", [
        {"role": "system", "content": "Synthetic calculator QA"},
        {"role": "user", "content": QUESTION},
    ])
    if captured:
        messages = freshness.with_answer_policy(
            messages, captured["model"], current_fact=bool(current_request),
        )
    context = ToolContext(user_id="synthetic", session_id="synthetic")
    events = [event async for event in agent.run_turn(
        "synthetic/model", messages, tools, context,
        preflight_tool=captured.get("preflight_tool", "calculate"),
        calculation_required=captured.get("calculation_required", True),
        calculation_expression=captured.get("calculation_expression"),
        strict_local=captured.get("strict_local", False),
        freshness_request=current_request,
        preset_call=preset_call,
    )]
    return context, snapshots, events


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_offline_current_rate_calculation_never_releases_guessed_operand(monkeypatch, strict):
    captured = await _routed_turn(monkeypatch, strict=strict, question=QUESTION)
    assert captured["calculation_required"] is True
    assert captured["preflight_tool"] == "calculate"
    assert captured["freshness_request"] == QUESTION
    assert captured["preset_call"] is None
    assert "web_search" not in captured["names"]

    context, _, events = await _execute(monkeypatch, [
        {"calls": [_call("calculate", {"expression": "(100+20)*1300"})]},
        {"text": GUESSED_ANSWER},
    ], tools=captured["tools"], captured=captured)

    assert context.tool_calls == {"calculate": 1}
    visible = _visible(events)
    assert visible.strip()
    assert "156000" not in visible
    assert "1300" not in visible


@pytest.mark.asyncio
async def test_failed_current_lookup_stops_before_any_model_or_calculation(monkeypatch):
    search = _tool("web_search", ToolResult(
        content="Lookup unavailable", failed=True, final_text=GUESSED_ANSWER,
    ))
    context, snapshots, events = await _execute(
        monkeypatch, [], tools=[CALCULATE, search],
        preset_call=("web_search", {"query": "synthetic current exchange rate"}),
    )
    assert snapshots == []
    assert context.tool_calls == {"web_search": 1}
    visible = _visible(events)
    assert visible.strip()
    assert "156000" not in visible
    assert "1300" not in visible
    assert sum(event["type"] == "delta" for event in events) == 1


@pytest.mark.asyncio
async def test_current_lookup_then_verified_calculation_preserves_supported_answer(monkeypatch):
    search = _tool("web_search", ToolResult(
        content=(
            "[1] Synthetic quote\nhttps://example.test/quote\n"
            "USD/KRW: 1300 KRW per USD, as of 2026-09-13T00:00:00Z."
        ),
        search_evidence=SearchEvidence(("https://example.test/quote",)),
    ))
    context, snapshots, events = await _execute(monkeypatch, [
        {"calls": [_call("calculate", {"expression": "(100+20)*1300"})]},
        {"text": "제공된 조회 결과의 환율로 계산한 합계는 156000원입니다."},
    ], tools=[CALCULATE, search],
        preset_call=("web_search", {"query": "synthetic current exchange rate"}),
    )
    assert context.tool_calls == {"web_search": 1, "calculate": 1}
    assert len(snapshots) == 2
    assert _visible(events) == "제공된 조회 결과의 환율로 계산한 합계는 156000원입니다."


@pytest.mark.asyncio
async def test_missing_calculation_call_emits_only_one_hold_delta(monkeypatch):
    context, snapshots, events = await _execute(monkeypatch, [
        {"text": GUESSED_ANSWER}, {"text": GUESSED_ANSWER},
    ], tools=[CALCULATE])
    assert len(snapshots) == 2
    assert context.tool_calls == {}
    visible = _visible(events)
    assert visible.strip()
    assert "156000" not in visible
    assert "1300" not in visible
    assert sum(event["type"] == "delta" for event in events) == 1


@pytest.mark.asyncio
async def test_current_lookup_does_not_unlock_an_unverified_calculation(monkeypatch):
    search = _tool("web_search", ToolResult(
        content=(
            "[1] Synthetic quote\nhttps://example.test/quote\n"
            "USD/KRW: 1300 KRW per USD, as of 2026-09-13T00:00:00Z."
        ),
        search_evidence=SearchEvidence(("https://example.test/quote",)),
    ))
    context, snapshots, events = await _execute(monkeypatch, [
        {"calls": [_call("calculate", {"expression": "120/0"})]},
        {"text": GUESSED_ANSWER}, {"text": GUESSED_ANSWER},
    ], tools=[CALCULATE, search],
        preset_call=("web_search", {"query": "synthetic current exchange rate"}),
    )
    assert context.tool_calls == {"web_search": 1, "calculate": 1}
    assert len(snapshots) == 3
    visible = _visible(events)
    assert visible.strip()
    assert "156000" not in visible
    assert "1300" not in visible
    assert sum(event["type"] == "delta" for event in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("question,expression,answer,required", [
    (
        "환율은 1달러에 1300원이라고 제공했어. "
        "이 값으로 100달러와 20달러의 합계 원화 금액을 계산해줘.",
        "(100+20)*1300", "156000", True,
    ),
    ("1+1", "1+1", "2", True),
    ("1+1은?", "1+1", "2", False),
])
async def test_supplied_operands_and_plain_math_keep_the_normal_answer(
    monkeypatch, question, expression, answer, required,
):
    captured = await _routed_turn(monkeypatch, strict=False, question=question)
    assert captured["freshness_request"] is None
    assert captured["calculation_required"] is required
    assert captured["preflight_tool"] == ("calculate" if required else None)
    plans = [{"text": answer}]
    if required and captured["calculation_expression"] is None:
        plans.insert(0, {"calls": [_call("calculate", {"expression": expression})]})
    context, _, events = await _execute(
        monkeypatch, plans, tools=captured["tools"], captured=captured,
    )
    assert context.tool_calls == ({"calculate": 1} if required else {})
    assert _visible(events) == answer
