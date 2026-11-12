"""A connector with many tools goes out by name until the model loads the schemas it needs;
small connectors, built-ins and the tools a turn must call at once go out whole; self-hosted
writers get every schema unless the switch says otherwise."""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services import agent
from app.services.tools import deferred
from app.services.tools.base import Tool, ToolContext, ToolResult, to_openai

EXTERNAL, SELF_HOSTED = "deepseek/deepseek-v4.1-flash", "local/qwen3.8-27b"


def _tool(name, ran=None, description="synthetic tool. More detail that is long."):
    async def run(arguments):
        if ran is not None:
            ran.append((name, arguments))
        return ToolResult(content=f"RESULT {name} https://example.com/{name}")

    return Tool(name=name, description=description,
                parameters={"type": "object", "properties": {"q": {"type": "string"}}},
                run=run, label=name, source="builtin" if "__" not in name else "big")


def _tools(ran=None, big=8, small=2):
    return ([_tool("calculate", ran)]
            + [_tool(f"mcp__big__t{i}", ran) for i in range(big)]
            + [_tool(f"mcp__small__s{i}", ran) for i in range(small)])


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(settings, "connector_tools_eager_max", 6)
    monkeypatch.setattr(settings, "self_hosted_defer_connector_tools", False)


def test_only_the_large_connector_is_deferred():
    tools = _tools()
    plan = deferred.plan(EXTERNAL, tools, to_openai(tools))
    assert plan is not None
    assert plan.deferred == [f"mcp__big__t{i}" for i in range(8)]
    hop_tools, hop_defs = plan.narrow(tools, to_openai(tools))
    names = [t.name for t in hop_tools]
    assert names == ["calculate", "mcp__small__s0", "mcp__small__s1", "load_tools"]
    assert [d["function"]["name"] for d in hop_defs] == names
    # name and one line each, not the schema
    loader = hop_defs[-1]["function"]
    assert "mcp__big__t3 — synthetic tool." in loader["description"]
    assert "More detail" not in loader["description"]
    assert '"q"' not in json.dumps(loader)


def test_the_payload_shrinks():
    tools = [_tool(f"mcp__big__t{i}", description="x" * 400) for i in range(20)]
    plan = deferred.plan(EXTERNAL, tools, to_openai(tools))
    _, narrowed = plan.narrow(tools, to_openai(tools))
    assert len(json.dumps(narrowed)) < len(json.dumps(to_openai(tools))) / 2


@pytest.mark.parametrize("count", [0, 6])
def test_small_connectors_and_built_ins_go_whole(count):
    tools = _tools(big=count)
    assert deferred.plan(EXTERNAL, tools, to_openai(tools)) is None


def test_self_hosted_writers_get_every_schema_unless_switched(monkeypatch):
    tools = _tools()
    assert deferred.plan(SELF_HOSTED, tools, None) is None
    assert deferred.plan("strict-local/qwen3.8-flash", tools, None) is None
    monkeypatch.setattr(settings, "self_hosted_defer_connector_tools", True)
    assert deferred.plan(SELF_HOSTED, tools, None) is not None


def test_tools_the_turn_must_call_at_once_stay_whole():
    tools = _tools()
    plan = deferred.plan(EXTERNAL, tools, None, eager={"mcp__big__t2"})
    assert "mcp__big__t2" not in plan.deferred
    names = [t.name for t in plan.narrow(tools, None)[0]]
    assert "mcp__big__t2" in names


def test_only_snapshot_bytes_are_sent():
    """The loader is built from the scanned snapshot: a tool dropped from it is not listed."""
    tools = _tools()
    snapshot = [d for d in to_openai(tools) if d["function"]["name"] != "mcp__big__t5"]
    plan = deferred.plan(EXTERNAL, tools, snapshot)
    assert "mcp__big__t5" not in plan.loader.description


@pytest.mark.asyncio
async def test_the_loader_returns_schemas_and_offers_them_next():
    tools = _tools()
    plan = deferred.plan(EXTERNAL, tools, to_openai(tools))
    result = await plan.loader.run({"names": ["mcp__big__t1", "nope"]})
    assert not result.failed and '"q"' in result.content and "nope" in result.content
    names = [t.name for t in plan.narrow(tools, None)[0]]
    assert "mcp__big__t1" in names and "mcp__big__t0" not in names
    bad = await plan.loader.run({"names": []})
    assert bad.failed and "mcp__big__t0" in bad.content


def _scripted(monkeypatch, hops, seen):
    async def completion(_model, messages, tools, *_args, **kwargs):
        step = hops[len(seen)]
        seen.append({"tools": [t.name for t in tools],
                     "defs": [d["function"]["name"] for d in kwargs.get("tool_definitions") or []],
                     "messages": messages[:]})
        acc = agent._Accumulator()
        acc.calls = dict(enumerate(step.get("calls", [])))
        acc.content = [step["text"]] if step.get("text") else []
        acc.usage = {"inputTokens": 1, "outputTokens": 1}
        for text in acc.content:
            yield "delta", text
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", completion)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 4)


def _call(name, arguments):
    return {"id": f"c_{name}", "name": name, "arguments": json.dumps(arguments)}


@pytest.mark.asyncio
async def test_a_turn_loads_then_calls_a_deferred_tool(monkeypatch):
    ran, seen = [], []
    _scripted(monkeypatch, [
        {"calls": [_call("load_tools", {"names": ["mcp__big__t4"]})]},
        {"calls": [_call("mcp__big__t4", {"q": "x"})]},
        {"text": "끝났습니다."},
    ], seen)
    tools = _tools(ran)
    events = [e async for e in agent.run_turn(
        EXTERNAL, [{"role": "user", "content": "큰 커넥터로 찾아 줘"}], tools,
        ToolContext(user_id="u", session_id="s", allowed={t.name for t in tools}),
        tool_definitions=to_openai(tools))]
    assert "load_tools" in seen[0]["defs"] and "mcp__big__t4" not in seen[0]["defs"]
    assert "mcp__big__t4" in seen[1]["defs"]
    assert ran == [("mcp__big__t4", {"q": "x"})]
    steps = [e for e in events if e["type"] == "step" and e.get("status") == "done"]
    assert [s["label"] for s in steps][:1] == ["도구 설명 읽기"]
    loaded = next(m for m in seen[1]["messages"] if m.get("name") == "load_tools")
    assert loaded["content"].startswith("불러온 도구 정의")
    assert "".join(e["text"] for e in events if e["type"] == "delta").startswith("끝났습니다")


@pytest.mark.asyncio
async def test_a_direct_call_to_a_deferred_tool_still_runs(monkeypatch):
    ran, seen = [], []
    _scripted(monkeypatch, [
        {"calls": [_call("mcp__big__t0", {"q": "y"})]},
        {"text": "답."},
    ], seen)
    tools = _tools(ran)
    await _drain(agent.run_turn(EXTERNAL, [{"role": "user", "content": "해 줘"}], tools,
                                ToolContext(user_id="u", session_id="s")))
    assert ran == [("mcp__big__t0", {"q": "y"})]
    assert "mcp__big__t0" in seen[1]["tools"]


@pytest.mark.asyncio
async def test_a_self_hosted_turn_sees_every_tool(monkeypatch):
    seen = []
    _scripted(monkeypatch, [{"text": "답."}], seen)
    tools = _tools()
    await _drain(agent.run_turn(SELF_HOSTED, [{"role": "user", "content": "해 줘"}], tools,
                                ToolContext(user_id="u", session_id="s")))
    assert seen[0]["tools"] == [t.name for t in tools]


async def _drain(stream):
    return [event async for event in stream]
