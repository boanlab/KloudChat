"""A duplicate successful arithmetic call retains evidence, not another execution."""

import asyncio
import json
from copy import deepcopy

import pytest

from app.services import agent
from app.services.tools.arithmetic import calculate
from app.services.tools.base import Tool, ToolContext, ToolResult


def _calls(*expressions):
    return [
        {"id": str(index), "name": "calculate", "arguments": json.dumps({"expression": expression})}
        for index, expression in enumerate(expressions)
    ]


def _model(monkeypatch, steps, snapshots):
    async def stream(_model, messages, *_args, **_kwargs):
        snapshots.append(deepcopy(messages))
        step = steps[len(snapshots) - 1]
        acc = agent._Accumulator()
        acc.calls = dict(enumerate(step.get("calls", [])))
        if text := step.get("text"):
            acc.content.append(text)
            yield "delta", text
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 3)


def _tool(ran, *, start=None, finish=None, source="builtin", read_only=True):
    async def run(arguments):
        ran.append(arguments["expression"])
        if start is not None:
            start.set()
            await finish.wait()
        return await calculate(arguments)

    return Tool(
        name="calculate",
        description="synthetic arithmetic",
        parameters={"type": "object"},
        run=run,
        label="calculate",
        source=source,
        read_only=read_only,
    )


async def _turn(tool, *, context=None, sanitizer=None):
    context = context or ToolContext(user_id="qa", session_id="qa", allowed={"calculate"})
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/model",
            [],
            [tool],
            context,
            preflight_tool="calculate",
            calculation_required=True,
            sanitize_tool_output=sanitizer,
        )
    ]
    return events, context


def _text(events):
    return "".join(event["text"] for event in events if event["type"] == "delta")


@pytest.mark.asyncio
async def test_concurrent_duplicate_waits_for_success_and_runs_only_once(monkeypatch):
    ran, snapshots = [], []
    start, finish = asyncio.Event(), asyncio.Event()
    _model(monkeypatch, [{"calls": _calls("12+3", "12+3")}, {"text": "15"}], snapshots)
    running = asyncio.create_task(_turn(_tool(ran, start=start, finish=finish)))
    await asyncio.wait_for(start.wait(), timeout=1)
    await asyncio.sleep(0)
    assert ran == ["12+3"]
    assert not running.done()
    finish.set()
    events, context = await asyncio.wait_for(running, timeout=1)
    assert _text(events) == "15"
    assert context.tool_calls == {"calculate": 1}
    evidence = [json.loads(row["content"]) for row in snapshots[-1] if row["role"] == "tool"]
    assert [row["exact"] for row in evidence] == ["15", "15"]


@pytest.mark.asyncio
async def test_cancellation_finishes_the_pending_duplicate_waiters(monkeypatch):
    ran, snapshots = [], []
    start, finish = asyncio.Event(), asyncio.Event()
    _model(monkeypatch, [{"calls": _calls("12+3", "12+3")}], snapshots)
    running = asyncio.create_task(_turn(_tool(ran, start=start, finish=finish)))
    await asyncio.wait_for(start.wait(), timeout=1)
    await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=1)
    assert ran == ["12+3"]


@pytest.mark.asyncio
async def test_whole_batch_retry_reuses_the_prior_successful_evidence(monkeypatch):
    ran, snapshots = [], []
    _model(
        monkeypatch,
        [
            {"calls": _calls("12+3", "1/0")},
            {"calls": _calls("12+3", "17*23")},
            {"text": "15, 391"},
        ],
        snapshots,
    )
    events, context = await _turn(_tool(ran))
    assert _text(events) == "15, 391"
    assert ran == ["12+3", "1/0", "17*23"]
    assert context.tool_calls == {"calculate": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
async def test_a_failed_duplicate_never_unlocks_the_arithmetic_gate(monkeypatch, retry):
    ran, snapshots = [], []
    steps = [{"calls": _calls("1/0")}] if retry else []
    steps.extend([{"calls": _calls("1/0", "1/0")}, {"text": "UNVERIFIED"}])
    _model(monkeypatch, steps, snapshots)
    events, context = await _turn(_tool(ran))
    assert "확정할 수 없습니다" in _text(events)
    assert "UNVERIFIED" not in _text(events)
    assert ran == ["1/0"]
    assert context.tool_calls == {"calculate": 1}


@pytest.mark.asyncio
async def test_cached_and_parallel_results_are_detached_before_masking(monkeypatch):
    ran, snapshots = [], []
    _model(
        monkeypatch,
        [
            {"calls": _calls("12+3", "1/0")},
            {"calls": _calls("12+3", "12+3", "17*23")},
            {"text": "15, 391"},
        ],
        snapshots,
    )

    def sanitizer(text):
        return text + " MASKED_ONCE", 0

    events, context = await _turn(_tool(ran), sanitizer=sanitizer)
    assert _text(events) == "15, 391"
    assert context.tool_calls == {"calculate": 3}
    evidence = [row["content"] for row in snapshots[-1] if row["role"] == "tool"]
    assert len(evidence) == 5
    assert all(row.count("MASKED_ONCE") == 1 for row in evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize("source,read_only", [("connector", True), ("builtin", False)])
async def test_only_builtin_readonly_arithmetic_is_eligible_for_evidence_reuse(
    monkeypatch, source, read_only
):
    ran, snapshots = [], []
    _model(monkeypatch, [{"calls": _calls("12+3", "12+3")}, {"text": "UNVERIFIED"}], snapshots)
    events, context = await _turn(_tool(ran, source=source, read_only=read_only))
    assert "확정할 수 없습니다" in _text(events)
    assert "UNVERIFIED" not in _text(events)
    assert context.tool_calls == {"calculate": 1}


@pytest.mark.asyncio
async def test_cached_results_do_not_survive_a_turn(monkeypatch):
    ran = []
    tool = _tool(ran)
    for _ in range(2):
        snapshots = []
        _model(monkeypatch, [{"calls": _calls("12+3", "12+3")}, {"text": "15"}], snapshots)
        events, context = await _turn(tool)
        assert _text(events) == "15"
        assert context.tool_calls == {"calculate": 1}
    assert ran == ["12+3", "12+3"]


@pytest.mark.asyncio
async def test_permission_is_rechecked_before_cached_evidence_is_used(monkeypatch):
    ran = []
    context = ToolContext(user_id="qa", session_id="qa", allowed={"calculate"})
    count = 0

    async def stream(*_args, **_kwargs):
        nonlocal count
        count += 1
        acc = agent._Accumulator()
        if count == 1:
            acc.calls = dict(enumerate(_calls("12+3", "1/0")))
        elif count == 2:
            context.allowed = {"other"}
            acc.calls = dict(enumerate(_calls("12+3", "17*23")))
        else:
            acc.content.append("UNVERIFIED")
            yield "delta", "UNVERIFIED"
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    events, _ = await _turn(_tool(ran), context=context)
    assert "UNVERIFIED" not in _text(events)
    assert ran == ["12+3", "1/0"]


@pytest.mark.asyncio
async def test_an_unverified_tool_payload_is_not_cached_as_arithmetic(monkeypatch):
    snapshots, ran = [], []

    async def run(_arguments):
        ran.append(True)
        return ToolResult(content='{"message":"no calculation evidence"}')

    tool = Tool(
        name="calculate",
        description="mock",
        parameters={"type": "object"},
        run=run,
        label="calculate",
    )
    _model(monkeypatch, [{"calls": _calls("12+3", "12+3")}, {"text": "UNVERIFIED"}], snapshots)
    events, context = await _turn(tool)
    assert "UNVERIFIED" not in _text(events)
    assert context.tool_calls == {"calculate": 1}
