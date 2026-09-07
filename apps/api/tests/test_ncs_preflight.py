"""A required preflight is an execution boundary, not a prose accuracy guarantee."""

from __future__ import annotations

import copy
import json

import pytest

from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult

PREFLIGHT = "check_ncs_answer"
REFUSAL = "문항 검산 절차를 완료하지 못해 정답이나 채점을 확정할 수 없습니다. 다시 시도해 주세요."


def _call(name=PREFLIGHT, arguments="{}"):
    return {"id": f"call_{name}", "name": name, "arguments": arguments}


def _tool(name, ran, *, failed=False):
    async def run(arguments):
        ran.append((name, arguments))
        return ToolResult(content=json.dumps({"value": "80"}), failed=failed)

    return Tool(
        name=name,
        description="synthetic",
        parameters={"type": "object"},
        run=run,
        label="검산 중",
        title="검산",
        read_only=name != "write_canary",
    )


def _stream(monkeypatch, steps, snapshots):
    async def stream_once(_model, messages, tools, *_args, **kwargs):
        snapshots.append((copy.deepcopy(messages), [tool.name for tool in tools], kwargs))
        step = steps[len(snapshots) - 1]
        acc = agent._Accumulator()
        for text in step.get("text", []):
            acc.content.append(text)
            yield "delta", text
        if step.get("exception"):
            raise agent.ChatStreamError("synthetic_stream_failure")
        acc.calls = dict(enumerate(step.get("calls", [])))
        acc.looped = step.get("looped", False)
        acc.runaway = step.get("runaway")
        acc.usage = {"inputTokens": 7, "outputTokens": 11}
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream_once)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 3)


async def _turn(tools, **kwargs):
    return [
        event
        async for event in agent.run_turn(
            "synthetic/model",
            [{"role": "user", "content": "Synthetic exercise"}],
            tools,
            ToolContext(user_id="synthetic-user", session_id="synthetic-session"),
            **kwargs,
        )
    ]


def _text(events):
    return "".join(event["text"] for event in events if event["type"] == "delta")


@pytest.mark.asyncio
async def test_unavailable_preflight_fails_before_any_model_call(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("an unavailable preflight reached the model")
        yield

    monkeypatch.setattr(agent, "_stream_once", forbidden)
    with pytest.raises(agent.ChatStreamError, match="preflight"):
        await _turn([], preflight_tool=PREFLIGHT)


@pytest.mark.asyncio
async def test_preflight_forces_first_hop_and_discards_every_tool_hop_draft(monkeypatch):
    ran, snapshots = [], []
    tools = [_tool(PREFLIGHT, ran), _tool("calculate", ran), _tool("web_search", ran)]
    _stream(
        monkeypatch,
        [
            {"text": ["UNVERIFIED_82.5"], "calls": [_call()]},
            {"text": ["ANOTHER_UNVERIFIED_DRAFT"], "calls": [_call("web_search")]},
            {"text": ["정답은 ", "80입니다."]},
        ],
        snapshots,
    )
    events = await _turn(tools, preflight_tool=PREFLIGHT, force_tool="web_search")
    assert snapshots[0][2]["force_tool"] == PREFLIGHT
    assert snapshots[1][2]["force_tool"] == "web_search"
    assert "force_tool" not in snapshots[2][2]
    assert [name for name, _ in ran] == [PREFLIGHT, "web_search"]
    assert _text(events) == "정답은 80입니다."
    assert not any(event["type"] == "retract" for event in events)
    assert [message["role"] for message in snapshots[1][0]][-2:] == ["assistant", "tool"]
    assert "UNVERIFIED" not in json.dumps(snapshots[1][0])
    assert "UNVERIFIED" not in json.dumps(snapshots[2][0])
    assert [event for event in events if event["type"] == "usage"] == [
        {"type": "usage", "inputTokens": 21, "outputTokens": 33},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("calls", [[], [_call("write_canary")], [_call(), _call("write_canary")]])
async def test_ignored_or_mixed_preflight_releases_no_draft_and_executes_nothing(
    monkeypatch, calls
):
    ran, snapshots = [], []
    _stream(monkeypatch, [{"text": ["UNVERIFIED_82.5"], "calls": calls}], snapshots)
    events = await _turn(
        [_tool(PREFLIGHT, ran), _tool("write_canary", ran)], preflight_tool=PREFLIGHT
    )
    assert ran == []
    assert len(snapshots) == 1
    assert _text(events) == REFUSAL
    assert any(event["type"] == "step" and event["status"] == "error" for event in events)
    assert [event for event in events if event["type"] == "usage"] == [
        {"type": "usage", "inputTokens": 7, "outputTokens": 11},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [{"looped": True}, {"runaway": "x"}])
@pytest.mark.parametrize("after_preflight", [False, True])
async def test_loop_and_runaway_boundaries_never_release_held_text(
    monkeypatch, flag, after_preflight
):
    ran, snapshots = [], []
    steps = [{"calls": [_call()]}] if after_preflight else []
    steps.append({"text": ["UNVERIFIED_82.5"], **flag})
    _stream(monkeypatch, steps, snapshots)
    events = await _turn([_tool(PREFLIGHT, ran)], preflight_tool=PREFLIGHT)
    assert _text(events) == REFUSAL
    assert "UNVERIFIED" not in json.dumps(events)
    assert not any(event["type"] == "retract" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("after_preflight", [False, True])
async def test_stream_failure_never_leaks_a_held_preflight_draft(monkeypatch, after_preflight):
    ran, snapshots, events = [], [], []
    steps = [{"calls": [_call()]}] if after_preflight else []
    steps.append({"text": ["UNVERIFIED_82.5"], "exception": True})
    _stream(monkeypatch, steps, snapshots)
    with pytest.raises(agent.ChatStreamError, match="synthetic_stream_failure"):
        async for event in agent.run_turn(
            "synthetic/model",
            [],
            [_tool(PREFLIGHT, ran)],
            ToolContext(user_id="user", session_id="session"),
            preflight_tool=PREFLIGHT,
        ):
            events.append(event)
    assert len(ran) == int(after_preflight)
    assert _text(events) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", ["allowed", "hop_limit"])
async def test_preflight_permissions_and_hop_budget_fail_before_the_model(monkeypatch, restricted):
    ran, snapshots = [], []
    _stream(monkeypatch, [], snapshots)
    ctx = ToolContext(user_id="user", session_id="session")
    if restricted == "allowed":
        ctx.allowed = {"some_other_tool"}
    else:
        monkeypatch.setattr(agent.settings, "max_tool_hops", 0)
    with pytest.raises(agent.ChatStreamError, match="preflight"):
        async for _ in agent.run_turn(
            "synthetic/model",
            [],
            [_tool(PREFLIGHT, ran)],
            ctx,
            preflight_tool=PREFLIGHT,
        ):
            pytest.fail("unavailable preflight produced an event")
    assert ran == [] and snapshots == []


@pytest.mark.asyncio
async def test_a_hallucinated_call_in_the_closing_hop_keeps_its_draft_private(monkeypatch):
    ran, snapshots = [], []
    _stream(
        monkeypatch,
        [
            {"calls": [_call()]},
            {"text": ["UNVERIFIED_82.5"], "calls": [_call()]},
            {"text": ["UNVERIFIED_82.5"], "calls": [_call()]},
        ],
        snapshots,
    )
    monkeypatch.setattr(agent.settings, "max_tool_hops", 1)
    events = await _turn([_tool(PREFLIGHT, ran)], preflight_tool=PREFLIGHT)
    assert len(ran) == 1
    assert snapshots[-1][1] == []
    assert _text(events) == REFUSAL


@pytest.mark.asyncio
async def test_a_failed_preflight_tool_can_retry_under_the_existing_loop(monkeypatch):
    ran, snapshots = [], []
    _stream(
        monkeypatch,
        [
            {"text": ["UNVERIFIED_DRAFT"], "calls": [_call(arguments="not-json")]},
            {"calls": [_call()]},
            {"text": ["조건을 확인했습니다."]},
        ],
        snapshots,
    )
    events = await _turn([_tool(PREFLIGHT, ran)], preflight_tool=PREFLIGHT)
    assert len(ran) == 1
    assert _text(events) == "조건을 확인했습니다."
    assert [event["status"] for event in events if event["type"] == "step"] == [
        "running",
        "error",
        "running",
        "done",
    ]


@pytest.mark.asyncio
async def test_requested_search_waits_for_a_successful_preflight_retry(monkeypatch):
    ran, snapshots = [], []
    _stream(
        monkeypatch,
        [
            {"calls": [_call(arguments="not-json")]},
            {"calls": [_call()]},
            {"calls": [_call("web_search")]},
            {"text": ["최종 답변"]},
        ],
        snapshots,
    )
    events = await _turn(
        [_tool(PREFLIGHT, ran), _tool("web_search", ran)],
        preflight_tool=PREFLIGHT,
        force_tool="web_search",
    )
    assert [snapshot[2].get("force_tool") for snapshot in snapshots] == [
        PREFLIGHT,
        PREFLIGHT,
        "web_search",
        None,
    ]
    assert [name for name, _ in ran] == [PREFLIGHT, "web_search"]
    assert _text(events) == "최종 답변"


@pytest.mark.asyncio
@pytest.mark.parametrize("calls", [[], [_call("write_canary")]])
async def test_a_failed_gate_cannot_be_bypassed_by_an_answer_or_another_tool(monkeypatch, calls):
    ran, snapshots = [], []
    _stream(
        monkeypatch,
        [
            {"calls": [_call()]},
            {"text": ["UNVERIFIED_82.5"], "calls": calls},
        ],
        snapshots,
    )
    events = await _turn(
        [_tool(PREFLIGHT, ran, failed=True), _tool("write_canary", ran)],
        preflight_tool=PREFLIGHT,
    )
    assert [name for name, _ in ran] == [PREFLIGHT]
    assert [snapshot[2].get("force_tool") for snapshot in snapshots] == [PREFLIGHT, PREFLIGHT]
    assert _text(events) == REFUSAL
    assert "UNVERIFIED" not in json.dumps(events)
    assert [event["status"] for event in events if event["type"] == "step"] == [
        "running",
        "error",
        "error",
    ]


@pytest.mark.asyncio
async def test_an_exhausted_failed_preflight_does_not_publish_the_closing_answer(monkeypatch):
    ran, snapshots = [], []
    _stream(
        monkeypatch,
        [
            {"calls": [_call()]},
            {"calls": [_call()]},
            {"text": ["UNVERIFIED_82.5"]},
        ],
        snapshots,
    )
    monkeypatch.setattr(agent.settings, "max_tool_hops", 1)
    events = await _turn([_tool(PREFLIGHT, ran, failed=True)], preflight_tool=PREFLIGHT)
    assert len(ran) == 1
    assert snapshots[-1][1] == []
    assert "force_tool" not in snapshots[-1][2]
    assert _text(events) == REFUSAL


@pytest.mark.asyncio
async def test_ordinary_turns_still_stream_before_the_model_finishes(monkeypatch):
    ran, snapshots = [], []
    _stream(monkeypatch, [{"text": ["first", "second"]}], snapshots)
    stream = agent.run_turn(
        "synthetic/model",
        [],
        [_tool("web_search", ran)],
        ToolContext(user_id="user", session_id="session"),
        force_tool="web_search",
    )
    assert await anext(stream) == {"type": "delta", "text": "first"}
    assert snapshots[0][2]["force_tool"] == "web_search"
    assert await anext(stream) == {"type": "delta", "text": "second"}
    rest = [event async for event in stream]
    assert [event["type"] for event in rest] == ["usage"]
