"""A lit globe means the answer was looked up.

`tool_choice: auto` is the right default for a tool loop and the wrong one for
a control somebody switched on. The system turn already carried the intent —
"사용자가 웹 검색을 켰습니다. 답변하기 전에 web_search 를 …" — and a small
model read it as advice. It answered from training data, and the answer arrived
under a lit globe with nothing distinguishing it from one that had been checked.
Somebody who happened to know the subject typed 인터넷 검색해봐, and the next
turn searched and corrected itself. That recovery only ever reaches the facts
the reader already doubted.

So the first hop is forced, and only the first: the requirement is that the
model looks before it writes, not that it keeps looking. A turn forced at every
hop cannot terminate — each one would owe another call.

The other half is what the model is told once the call is made. The old wording
asked for one search per turn, and one search per turn is what it got: asked
about hardware and the software that runs on it, the model looked up the
hardware, got it right, and wrote the software list underneath from memory. The
rule is per-claim now, and these pin both halves.
"""

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
    # Forced at every hop the turn never ends: each answer owes another call.
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
    # An agent allowlist can remove the search tool from a turn whose toggle is
    # lit. Naming it in `tool_choice` anyway is a request the upstream rejects,
    # which would turn a missing tool into a failed turn.
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

    # The wording that produced minimal compliance: one search on the narrowest
    # sub-question, and the rest of the answer written from memory.
    assert "최소 한 번" not in prompt
    assert "축마다" in prompt
    # Searching is not enough on its own — the model has to prefer what it read
    # over what it remembers, and mark what it could not check.
    assert "검색 결과를 따르세요" in prompt
    assert "확인하지 못했다고 밝히세요" in prompt


def test_a_toggle_with_no_tool_behind_it_still_says_so() -> None:
    prompt = context.system_prompt(
        SessionKind.chat, with_tools=True, web_search=True, web_search_available=False
    )

    assert "검색 도구가 없습니다" in prompt
    # Never both: the blocked notice tells the model not to try, and the nudge
    # tells it to search. Together they are a contradiction.
    assert "축마다" not in prompt
