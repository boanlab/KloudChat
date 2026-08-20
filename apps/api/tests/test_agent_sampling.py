"""What an agent's own settings reach.

An agent screen has always been able to name a model and drag a temperature,
and both were stored the moment they were set. Whether either one arrived at the
upstream call is a separate question, and for a long time the answer to the
second was no: `temperature` was a column, a slider and a badge with nothing
downstream of it. These pin the wiring so it cannot quietly come undone again —
one turn's worth of settings, all the way to the request body.
"""

from __future__ import annotations

import json

import pytest

from app.models.chat import ChatSession, Message
from app.models.user import User
from app.routers import sessions as sessions_router
from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult


class _Response:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _Client:
    """Records the request body instead of sending it."""

    def __init__(self, seen: list[dict]) -> None:
        self._seen = seen

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        return _Response(
            [
                'data: {"choices":[{"delta":{"content":"답"}}]}',
                "data: [DONE]",
            ]
        )


def _capture(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen)

    monkeypatch.setattr(agent, "_client", client)
    return seen


@pytest.mark.asyncio
async def test_the_agents_temperature_is_sent_with_the_turn(monkeypatch) -> None:
    seen = _capture(monkeypatch)

    _ = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
            temperature=0.2,
        )
    ]

    assert [payload["temperature"] for payload in seen] == [0.2]


@pytest.mark.asyncio
async def test_a_turn_with_no_agent_leaves_the_upstream_default_alone(monkeypatch) -> None:
    # Not "0.7 by default": a chat nobody attached an agent to has to keep
    # sampling exactly as it did before temperature was carried at all, or this
    # change would have rewritten every ordinary conversation on the instance.
    seen = _capture(monkeypatch)

    _ = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]

    assert seen and all("temperature" not in payload for payload in seen)


@pytest.mark.asyncio
async def test_every_hop_of_one_turn_samples_alike(monkeypatch) -> None:
    # A tool-calling turn is several requests. If only the first carried the
    # setting, an answer would change voice at the point a tool ran.
    async def runner(_arguments):
        return ToolResult(content="ok")

    tool = Tool(
        name="lookup",
        description="lookup",
        parameters={"type": "object", "properties": {}},
        run=runner,
        label="lookup",
    )
    seen: list[float | None] = []

    async def fake_stream_once(*_args, temperature=None, **_kwargs):
        seen.append(temperature)
        acc = agent._Accumulator()
        if len(seen) == 1:
            acc.calls[0] = {"id": "call_0", "name": "lookup", "arguments": "{}"}
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", fake_stream_once)

    _ = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
            temperature=0.1,
        )
    ]

    assert seen == [0.1, 0.1]


@pytest.mark.asyncio
async def test_the_router_hands_the_agents_temperature_to_the_loop(monkeypatch) -> None:
    """The other half of the wire: `_run_turn` is where the agent's value enters."""
    session = ChatSession(id="session-1", user_id="user-1", model="")
    user = User(
        id="user-1",
        email="person@example.test",
        password_hash="hash",
        name="Person",
    )
    added: list[object] = []
    seen: dict = {}

    class Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            if model is ChatSession and key == session.id:
                return session
            if model is User and key == user.id:
                return user
            return None

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    async def run_turn(*_args, **kwargs):
        seen.update(kwargs)
        yield {"type": "delta", "text": "답"}
        yield {"type": "usage", "inputTokens": 10, "outputTokens": 10}

    async def title(*_args, **_kwargs):
        return None, {"inputTokens": 0, "outputTokens": 0}

    async def enrich(**_kwargs):
        return None, None

    monkeypatch.setattr(sessions_router, "SessionLocal", Db)
    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions_router.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions_router, "_enrich", enrich)

    chunks = [
        chunk
        async for chunk in sessions_router._run_turn(
            user_id=user.id,
            api_key="virtual-key",
            auto_memory=False,
            session_id=session.id,
            model={
                "id": "vendor/model",
                "label": "vendor/model",
                "kinds": ["chat"],
                "dataBoundary": "external",
                "strictLocal": False,
                "inputCreditCost": 1,
                "creditCost": 1,
            },
            messages=[{"role": "user", "content": "안녕"}],
            tools=[],
            temperature=0.3,
            first_user_message="안녕",
            is_first_turn=True,
        )
    ]

    assert seen["temperature"] == 0.3
    assert any("답" in json.dumps(chunk, ensure_ascii=False) for chunk in chunks)
    assert any(isinstance(row, Message) for row in added)
