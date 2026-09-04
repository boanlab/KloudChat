"""Search toggle on: the first hop forces `web_search` and the prompt asks per-claim grounding."""

from __future__ import annotations

import pytest

from app.models.chat import SessionKind
from app.services import agent, context
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

    def __init__(self, seen: list[dict], scripted: list[list[str]]) -> None:
        self._seen = seen
        self._scripted = scripted

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        lines = (
            self._scripted.pop(0)
            if self._scripted
            else ['data: {"choices":[{"delta":{"content":"답"}}]}', "data: [DONE]"]
        )
        return _Response(lines)


def _capture(monkeypatch, scripted: list[list[str]] | None = None) -> list[dict]:
    seen: list[dict] = []
    script = list(scripted or [])

    async def client(*_args, **_kwargs):
        return _Client(seen, script)

    monkeypatch.setattr(agent, "_client", client)
    return seen


async def _run_search(_args):
    return ToolResult(content="검색 결과")


_SEARCH = Tool(
    name="web_search",
    description="웹 검색",
    parameters={"type": "object", "properties": {}},
    run=_run_search,
    label="웹 검색 중",
)

#: A first hop that calls the tool, so the loop reaches a second one.
_CALLS_SEARCH = [
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c0",'
    '"function":{"name":"web_search","arguments":"{}"}}]}}]}',
    "data: [DONE]",
]
_ANSWERS = ['data: {"choices":[{"delta":{"content":"답"}}]}', "data: [DONE]"]


def _ctx() -> ToolContext:
    return ToolContext(user_id="user", session_id="session", api_key="key")


# ── the first hop is forced ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_lit_toggle_forces_the_first_search(monkeypatch) -> None:
    seen = _capture(monkeypatch)

    _ = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "이 GPU 사양 알려줘"}],
            [_SEARCH],
            _ctx(),
            force_tool="web_search",
        )
    ]

    assert seen[0]["tool_choice"] == {"type": "function", "function": {"name": "web_search"}}


@pytest.mark.asyncio
async def test_only_the_first_hop_is_forced(monkeypatch) -> None:
    # Forced only on the first hop, or the turn never ends.
    seen = _capture(monkeypatch, [_CALLS_SEARCH, _ANSWERS])

    _ = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "이 GPU 사양 알려줘"}],
            [_SEARCH],
            _ctx(),
            force_tool="web_search",
        )
    ]

    assert len(seen) == 2
    assert seen[0]["tool_choice"] != "auto"
    assert seen[1]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_an_unlit_toggle_leaves_the_loop_alone(monkeypatch) -> None:
    seen = _capture(monkeypatch)

    _ = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "고마워"}],
            [_SEARCH],
            _ctx(),
        )
    ]

    assert seen[0]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_a_tool_that_is_not_there_is_not_forced(monkeypatch) -> None:
    # A tool absent from the agent allowlist must not be named in `tool_choice`.
    seen = _capture(monkeypatch)
    other = Tool(
        name="lookup",
        description="lookup",
        parameters={"type": "object", "properties": {}},
        run=_run_search,
        label="lookup",
    )

    _ = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [other],
            _ctx(),
            force_tool="web_search",
        )
    ]

    assert seen[0]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_a_turn_with_no_tools_sends_no_tool_choice(monkeypatch) -> None:
    seen = _capture(monkeypatch)

    _ = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [],
            _ctx(),
            force_tool="web_search",
        )
    ]

    assert seen and all("tool_choice" not in payload for payload in seen)


# ── and the rule it is forced under ────────────────────────────────────


def test_the_rule_asks_per_claim_not_per_turn() -> None:
    prompt = context.system_prompt(SessionKind.chat, with_tools=True, web_search=True)

    # Not one search per turn.
    assert "최소 한 번" not in prompt
    assert "축마다" in prompt
    # Prefer what was read; mark what could not be checked.
    assert "검색 결과를 따르세요" in prompt
    assert "확인하지 못했다고 밝히세요" in prompt


def test_a_toggle_with_no_tool_behind_it_still_says_so() -> None:
    prompt = context.system_prompt(
        SessionKind.chat, with_tools=True, web_search=True, web_search_available=False
    )

    assert "검색 도구가 없습니다" in prompt
    # The blocked notice and the search nudge never appear together.
    assert "축마다" not in prompt
