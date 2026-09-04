"""도구 호출 한도에 닿아도 답은 나온다.

A model that is still calling tools has written no prose yet, so stopping at
the cap used to leave the person with one line — 「도구 호출이 5회를 넘어
중단했습니다」 — and none of what five searches had found. The last hop now
runs without tools and is told to answer from what it has.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult


class _Response:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _Client:
    """Calls a tool on every hop until it is given none, then answers."""

    def __init__(self, seen: list[dict]) -> None:
        self._seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        if json.get("tools"):
            return _Response(
                [
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                    '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                    "data: [DONE]",
                ]
            )
        return _Response(
            [
                'data: {"choices":[{"delta":{"content":"모은 자료로 쓴 답"}}]}',
                "data: [DONE]",
            ]
        )


@pytest.mark.asyncio
async def test_the_cap_ends_in_an_answer_written_without_tools(monkeypatch) -> None:
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen)

    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(settings, "max_tool_hops", 2)

    async def lookup(_args):
        return ToolResult(content="찾은 것")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "확인해 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]

    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "모은 자료로 쓴 답" in text
    assert "중단했습니다" not in text
    # The closing call carried no tools and the instruction to answer.
    assert "tools" not in seen[-1]
    assert any("도구는 더 쓸 수 없습니다" in (m.get("content") or "") for m in seen[-1]["messages"])


@pytest.mark.asyncio
async def test_a_link_no_tool_returned_is_named_as_unverified(monkeypatch) -> None:
    """검색 결과에 없던 링크는 답 끝에 그렇다고 적힌다."""
    seen: list[dict] = []

    class _Once(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            return _Response(
                [
                    'data: {"choices":[{"delta":{"content":"근거: '
                    "https://arxiv.org/abs/2106.09685 와 "
                    'https://arxiv.org/abs/2208.04691 참고."}}]}',
                    "data: [DONE]",
                ]
            )

    async def client(*_args, **_kwargs):
        return _Once(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="LoRA 논문: https://arxiv.org/abs/2106.09685")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "선행연구 정리"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "검색·열람한 결과에 없던" in text
    assert "2208.04691" in text.split("없던")[1]
    assert "2106.09685" not in text.split("없던")[1]


@pytest.mark.asyncio
async def test_search_sources_are_kept_when_the_model_omits_links(monkeypatch) -> None:
    """검색한 답에는 모델이 잊어도 실제 도구 URL이 남는다."""
    seen: list[dict] = []

    class _SearchOnce(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"web_search","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            return _Response(
                [
                    'data: {"choices":[{"delta":{"content":'
                    '"검색 결과를 비교한 답입니다."}}]}',
                    "data: [DONE]",
                ]
            )

    async def client(*_args, **_kwargs):
        return _SearchOnce(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def search(_args):
        return ToolResult(
            content=(
                "정부 자료: https://example.go.kr/policy/2026\n"
                "연구 자료: https://journal.example.org/article/42"
            )
        )

    tool = Tool(
        name="web_search",
        description="검색한다",
        parameters={"type": "object"},
        run=search,
        label="웹 검색",
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "최신 자료를 검색해 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "### 확인한 출처" in text
    assert "example.go.kr · 2026" in text
    assert "https://example.go.kr/policy/2026" in text
    assert "https://journal.example.org/article/42" in text


@pytest.mark.asyncio
async def test_exact_long_paragraphs_are_kept_once(monkeypatch) -> None:
    """A model's copied closing paragraph is removed from screen and storage."""
    paragraph = "역발행의 초안 작성자와 법적 발급자를 구분해 설명합니다. " * 6
    answer_line = (
        'data: {"choices":[{"delta":{"content":'
        + json.dumps(f"{paragraph}\n\n{paragraph}", ensure_ascii=False)
        + "}}]}"
    )

    class _Repeated(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            return _Response([answer_line, "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Repeated([])

    monkeypatch.setattr(agent, "_client", client)
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "역발행을 설명해 줘"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    visible = ""
    for event in events:
        if event["type"] == "delta":
            visible += event["text"]
        elif event["type"] == "retract":
            visible = visible.replace(event["text"], "", 1)
    assert visible.count(paragraph) == 1


def test_official_sources_sort_before_secondary_sources():
    urls = [
        "https://news.example.com/story/1",
        "https://www.msit.go.kr/bbs/view.do?id=42",
        "https://university.ac.kr/research/7",
    ]
    assert sorted(urls, key=agent._source_priority)[0].startswith("https://www.msit.go.kr")


@pytest.mark.asyncio
async def test_an_institution_homepage_is_not_presented_as_direct_evidence(monkeypatch) -> None:
    seen: list[dict] = []

    class _HomepageCitation(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                        '"id":"c1","function":{"name":"web_search",'
                        '"arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            return _Response(
                [
                    'data: {"choices":[{"delta":{"content":'
                    '"기관 자료입니다: https://www.nia.or.kr"}}]}',
                    "data: [DONE]",
                ]
            )

    async def client(*_args, **_kwargs):
        return _HomepageCitation(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def search(_args):
        return ToolResult(content="기관 홈페이지: https://www.nia.or.kr")

    tool = Tool(
        name="web_search",
        description="검색한다",
        parameters={"type": "object"},
        run=search,
        label="웹 검색",
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "통계를 검증해 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert "직접 출처가 아닙니다" in text


@pytest.mark.asyncio
async def test_an_empty_completion_after_tools_is_asked_again(monkeypatch) -> None:
    """도구를 쓰고 빈 답이 오면 한 번 더 물어 답을 받는다."""
    seen: list[dict] = []

    class _Blank(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            if len(self._seen) == 2:
                return _Response(["data: [DONE]"])
            answer = 'data: {"choices":[{"delta":{"content":"찾은 것으로 답합니다."}}]}'
            return _Response([answer, "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Blank(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="찾은 것")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "찾아 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "찾은 것으로 답합니다."
    assert len(seen) == 3
    assert seen[2].get("tools") in (None, [])
    assert "답이 비어 있습니다" in seen[2]["messages"][-1]["content"]


def test_a_repeating_stream_is_recognised() -> None:
    """같은 문단이 네 번 나오면 되풀이로 본다. 짧은 답이나 다른 문장은 아니다."""
    paragraph = "역발행은 공급받는 자가 세금계산서를 발행하는 방식이며 절차가 다릅니다. " * 3
    assert agent._is_looping([paragraph] * 8)
    assert not agent._is_looping([paragraph])
    different = [f"{i}번째 문장은 서로 다르며 되풀이가 아닙니다. " for i in range(60)]
    assert not agent._is_looping(different)


@pytest.mark.asyncio
async def test_a_looping_answer_is_cut_and_told(monkeypatch) -> None:
    """되풀이하는 스트림은 끊기고, 끝에 그렇다는 말이 붙는다."""
    seen: list[dict] = []
    paragraph = "역발행은 공급받는 자가 세금계산서를 발행하는 방식이며 절차가 다릅니다. " * 3
    chunk = json.dumps({"choices": [{"delta": {"content": paragraph}}]}, ensure_ascii=False)
    lines = [f"data: {chunk}"] * 40 + ["data: [DONE]"]

    class _Loop(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            return _Response(lines)

    async def client(*_args, **_kwargs):
        return _Loop(seen)

    monkeypatch.setattr(agent, "_client", client)
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "역발행이 뭔가요"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "되풀이되어 여기서 멈췄습니다" in text
    assert text.count(paragraph) < 40
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried_before_it_is_reported(monkeypatch) -> None:
    """429 는 잠깐 기다렸다 다시 하고, 두 번째에 답이 오면 그 답을 쓴다."""
    seen: list[dict] = []
    waited: list[float] = []

    class _Limited(_Response):
        status_code = 429

        async def aread(self) -> bytes:
            return b"rate limited"

    class _Client429(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Limited([])
            return _Response(['data: {"choices":[{"delta":{"content":"답"}}]}', "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Client429(seen)

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(agent.asyncio, "sleep", sleep)
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "답"
    assert len(seen) == 2 and waited == [agent._RETRY_AFTER[0]]


@pytest.mark.asyncio
async def test_an_answer_whose_searches_all_came_back_empty_says_so(monkeypatch) -> None:
    """검색이 전부 빈손이면 답 밑에 확인하지 못했다고 적힌다."""
    seen: list[dict] = []

    class _Searching(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"web_search","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            answer = 'data: {"choices":[{"delta":{"content":"Twenge (2018) 은 ..."}}]}'
            return _Response([answer, "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Searching(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def search(_args):
        return ToolResult(content="검색 결과가 질문과 무관한 것뿐입니다", empty=True)

    tool = Tool(
        name="web_search",
        description="검색",
        parameters={"type": "object"},
        run=search,
        label="검색",
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "선행연구 정리"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text.startswith("Twenge (2018)")
    assert "검색으로 확인하지 못했습니다" in text


@pytest.mark.asyncio
async def test_narration_before_a_tool_is_taken_back(monkeypatch) -> None:
    """도구를 부르며 한 짧은 말은 답에서 빠지고, 화면에도 retract 로 알린다."""
    seen: list[dict] = []

    class _Narrating(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"content":"검색해 보겠습니다."}}]}',
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            return _Response(
                ['data: {"choices":[{"delta":{"content":"답입니다."}}]}', "data: [DONE]"]
            )

    async def client(*_args, **_kwargs):
        return _Narrating(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="찾은 것")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "찾아 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    retracted = [e["text"] for e in events if e["type"] == "retract"]
    assert retracted == ["검색해 보겠습니다."]
    # 모델에게는 제 말이 그대로 돌아간다 — 대화 맥락은 줄지 않는다.
    assert seen[1]["messages"][-1]["content"] == "찾은 것"
    assert "검색해 보겠습니다." in (seen[1]["messages"][-2].get("content") or "")


def test_an_answer_written_twice_around_a_tool_is_recognised() -> None:
    first = (
        "SELECT c.course_id, COUNT(*)\nFROM courses c JOIN enrollments e ON …\n"
        "GROUP BY c.course_id\nHAVING COUNT(*) >= 20\nORDER BY avg DESC;"
    )
    assert agent._repeats(first, "검증했습니다.\n\n" + first + "\n\n결과가 맞습니다.")
    assert not agent._repeats(first, "시뮬레이션 결과 두 과목이 남았습니다.")
