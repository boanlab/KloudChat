"""Verify the local arithmetic boundary, not a language model's answer quality."""

from __future__ import annotations

import copy
import json

import httpx
import pytest

from app.models.user import User
from app.routers import sessions as sessions_router
from app.services import agent, settings_store
from app.services.tools import builtin, registry
from app.services.tools.base import Tool, ToolContext


def _user() -> User:
    return User(email="calculator-test@example.test", password_hash="hash", name="Learner")


class _EmptyRows:
    def all(self):
        return []


class _EmptyDb:
    async def exec(self, _query):
        return _EmptyRows()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("arithmetic integration tests must not make network requests")

    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)


@pytest.fixture
def _unconfigured_backends(monkeypatch):
    async def empty():
        return settings_store.ToolBackends()

    monkeypatch.setattr(settings_store, "tools_config", empty)


@pytest.mark.asyncio
async def test_calculate_is_available_without_a_configured_tool_backend(_unconfigured_backends):
    tools = await builtin.available_builtins(web_search_enabled=True)
    by_name = {tool.name: tool for tool in tools}
    assert "calculate" in by_name
    assert {"execute_code", "fetch_url", "web_search"}.isdisjoint(by_name)
    result = await by_name["calculate"].run({"expression": "1600/20"})
    assert result.failed is False
    assert json.loads(result.content)["exact"] == "80"


@pytest.mark.asyncio
async def test_a_calculate_only_allowlist_does_not_enable_code_connectors_or_artifacts(monkeypatch):
    async def configured():
        return settings_store.ToolBackends(
            search="https://unused.test/search",
            fetch="https://unused.test/fetch",
            exec="https://unused.test/exec",
        )

    async def never_run(_arguments):
        pytest.fail("an unselected connector must not execute")

    async def connectors(*_args):
        return [
            Tool(
                name="mcp__example__write",
                description="synthetic connector",
                parameters={"type": "object"},
                run=never_run,
                label="connector",
                source="example",
                read_only=False,
            )
        ]

    monkeypatch.setattr(settings_store, "tools_config", configured)
    monkeypatch.setattr(registry, "connector_tools", connectors)
    tools = await registry.build_tools(_EmptyDb(), _user(), web_search=True, allowed=["calculate"])
    assert [tool.name for tool in tools] == ["calculate"]
    assert tools[0].read_only is True
    assert tools[0].source == "builtin"
    assert tools[0].wants_context is False
    assert await registry.build_tools(_EmptyDb(), _user(), web_search=True, allowed=[]) == []


@pytest.mark.asyncio
async def test_strict_local_calculate_does_not_read_settings_or_resolve_network_tools(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("strict-local arithmetic touched settings or a remote registry")

    monkeypatch.setattr(settings_store, "tools_config", forbidden)
    monkeypatch.setattr(registry, "available_builtins", forbidden)
    monkeypatch.setattr(registry, "connector_tools", forbidden)
    tools = await registry.build_tools(
        object(),
        _user(),
        web_search=True,
        allowed=["calculate"],
        strict_local=True,
        knowledge_collection="synthetic-remote-index",
    )
    assert [tool.name for tool in tools] == ["calculate"]
    assert [tool.name for tool in sessions_router._strict_local_tools(tools)] == ["calculate"]
    result = await tools[0].run({"expression": "(12*70+8*95)/(12+8)"})
    assert json.loads(result.content)["value"] == "80"
    assert (
        await registry.build_tools(
            object(),
            _user(),
            web_search=True,
            allowed=[],
            strict_local=True,
        )
        == []
    )


@pytest.mark.asyncio
async def test_tool_catalog_exposes_the_available_local_calculator(_unconfigured_backends):
    rows = await registry.tool_catalog(_EmptyDb(), _user())
    calculators = [row for row in rows if row["name"] == "calculate"]
    assert len(calculators) == 1
    assert calculators[0]["available"] is True
    assert calculators[0]["label"]


async def _tool_turn(monkeypatch, tools, arguments):
    snapshots = []

    async def mock_stream_once(_model, messages, hop_tools, *_args, **_kwargs):
        snapshots.append((copy.deepcopy(messages), [tool.name for tool in hop_tools]))
        acc = agent._Accumulator()
        if len(snapshots) == 1:
            acc.calls[0] = {
                "id": "calculation_0",
                "name": "calculate",
                "arguments": json.dumps(arguments),
            }
        else:
            acc.content.append("Synthetic final response; model accuracy is not under test.")
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", mock_stream_once)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 2)
    ctx = ToolContext(
        user_id="synthetic-user", session_id="synthetic-session", allowed={"calculate"}
    )
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/model",
            [{"role": "user", "content": "Check the supplied arithmetic."}],
            tools,
            ctx,
            strict_local=True,
        )
    ]
    assert len(snapshots) == 2
    tool_messages = [message for message in snapshots[1][0] if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["name"] == "calculate"
    assert ctx.pending_artifacts == [] and ctx.pending_notes == []
    return tool_messages[0]["content"], events, snapshots


@pytest.mark.asyncio
async def test_real_calculation_and_independent_grading_reach_the_next_model_hop(monkeypatch):
    tools = await registry.build_tools(
        object(),
        _user(),
        web_search=False,
        allowed=["calculate"],
        strict_local=True,
    )
    content, events, snapshots = await _tool_turn(
        monkeypatch,
        tools,
        {
            "expression": "(12*70+8*95)/20",
            "choices": ["80", "81", "82.5", "85"],
            "submitted_choice": 3,
        },
    )
    data = json.loads(content)
    assert data["exact"] == data["value"] == "80"
    assert data["choice_status"] == "unique"
    assert data["matched_choices"] == [1]
    assert data["answer"] == 1
    assert data["grading"] == "incorrect"
    assert snapshots[0][1] == ["calculate"]
    assert [(event["status"]) for event in events if event["type"] == "step"] == ["running", "done"]


@pytest.mark.asyncio
@pytest.mark.parametrize("expression", ["1600/0", "__import__('os').system('forbidden')"])
async def test_invalid_arithmetic_reaches_the_next_model_hop_as_a_failure(monkeypatch, expression):
    tools = await registry.build_tools(
        object(),
        _user(),
        web_search=False,
        allowed=["calculate"],
        strict_local=True,
    )
    content, events, _ = await _tool_turn(monkeypatch, tools, {"expression": expression})
    data = json.loads(content)
    assert data["error"] == "invalid_calculation"
    assert "exact" not in data and "answer" not in data
    assert [event["status"] for event in events if event["type"] == "step"] == ["running", "error"]


@pytest.mark.asyncio
async def test_a_hallucinated_calculator_is_never_executed_without_permission(monkeypatch):
    from app.services.tools.arithmetic import CALCULATE

    async def forbidden(_arguments):
        pytest.fail("calculate executed although the turn has no allowed tools")

    monkeypatch.setattr(CALCULATE, "run", forbidden)
    tools = await registry.build_tools(
        object(),
        _user(),
        web_search=False,
        allowed=[],
        strict_local=True,
    )
    content, events, snapshots = await _tool_turn(monkeypatch, tools, {"expression": "1600/20"})
    assert snapshots[0][1] == []
    assert "알 수 없는 도구 calculate" in content
    assert [event["status"] for event in events if event["type"] == "step"] == ["running", "error"]
