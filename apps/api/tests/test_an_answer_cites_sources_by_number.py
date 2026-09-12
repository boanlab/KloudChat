"""The model cites search results as `[n]`; the server numbers the results across
the turn, turns the citations into links and lists the cited sources."""

from __future__ import annotations

import json

import pytest

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
    def __init__(self, seen: list[dict], scripted: list[list[str]]) -> None:
        self._seen = seen
        self._scripted = scripted

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        return _Response(self._scripted.pop(0))


def _content(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


def _calls(name: str, arguments: dict, call_id: str = "c0") -> list[str]:
    return [
        "data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": call_id,
                                    "function": {"name": name, "arguments": json.dumps(arguments)},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]


_SEARCHES = iter(
    [
        "'첫 검색' 검색 결과:\n\n[1] 정책 문서\nhttps://example.go.kr/policy/2026\n요약\n\n[2] 기사\nhttps://news.example.com/a/1\n요약\n",
        "'둘째 검색' 검색 결과:\n\n[1] 논문\nhttps://journal.example.org/article/42\n요약\n\n[2] 기사\nhttps://news.example.com/a/1\n요약\n",
    ]
)


async def _run_search(_args):
    return ToolResult(content=next(_SEARCHES))


async def _run_fetch(_args):
    return ToolResult(content="페이지 본문입니다.")


_TOOLS = [
    Tool(
        name="web_search",
        description="웹 검색",
        parameters={"type": "object"},
        run=_run_search,
        label="웹 검색",
    ),
    Tool(
        name="fetch_url",
        description="페이지 읽기",
        parameters={"type": "object"},
        run=_run_fetch,
        label="읽기",
    ),
]


def _ctx() -> ToolContext:
    return ToolContext(user_id="user", session_id="session", api_key="key")


async def _collect(monkeypatch, scripted: list[list[str]], tools: list[Tool] = _TOOLS):
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen, scripted)

    monkeypatch.setattr(agent, "_client", client)
    events = []
    async for event in agent.run_turn("m", [{"role": "user", "content": "q"}], tools, _ctx()):
        events.append(event)
    return seen, events


def _final_text(events: list[dict]) -> str:
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            text = text.replace(event["text"], "", 1)
    return text


@pytest.mark.asyncio
async def test_numbers_run_across_searches_and_citations_become_links(monkeypatch) -> None:
    answer = [
        _content("정책은 이렇습니다[1]. 논문은 다릅니다 [3]. "),
        _content("둘 다 보도됐습니다 [2, 3]. 없는 번호 [9]는 그대로."),
        "data: [DONE]",
    ]
    seen, events = await _collect(
        monkeypatch,
        [
            _calls("web_search", {"query": "첫 검색"}),
            _calls("web_search", {"query": "둘째 검색"}, "c1"),
            answer,
        ],
    )
    # The second search's entries were renumbered: its [1] became [3], its [2] stays [2].
    tool_messages = [m["content"] for m in seen[2]["messages"] if m.get("role") == "tool"]
    assert "[3] 논문\nhttps://journal.example.org/article/42" in tool_messages[1]
    assert "[2] 기사\nhttps://news.example.com/a/1" in tool_messages[1]

    text = _final_text(events)
    assert "이렇습니다[[1]](https://example.go.kr/policy/2026)." in text
    assert "다릅니다 [[3]](https://journal.example.org/article/42)." in text
    assert (
        "[[2]](https://news.example.com/a/1)[[3]](https://journal.example.org/article/42)." in text
    )
    assert "[9]는 그대로" in text
    assert "### 출처" in text
    assert "- [1] [example.go.kr · 2026](https://example.go.kr/policy/2026)" in text
    assert "- [3] [journal.example.org · 42](https://journal.example.org/article/42)" in text
    assert "### 확인한 출처" not in text
    assert "검색·열람한 결과에 없던" not in text


@pytest.mark.asyncio
async def test_a_fetched_page_gets_its_own_number(monkeypatch) -> None:
    answer = [_content("본문에 따르면 그렇습니다 [1]."), "data: [DONE]"]
    seen, events = await _collect(
        monkeypatch, [_calls("fetch_url", {"url": "https://docs.example.com/guide"}), answer]
    )
    tool_message = next(m for m in seen[1]["messages"] if m.get("role") == "tool")
    assert tool_message["content"].startswith(
        "[1] https://docs.example.com/guide\n\n페이지 본문입니다."
    )
    text = _final_text(events)
    assert "[[1]](https://docs.example.com/guide)" in text


def test_citations_inside_code_blocks_are_left_alone() -> None:
    linked, cited = agent._link_citations(
        "값 [1]\n```\narr[1]\n```\n끝 [1-2]", ["https://a.test/x", "https://b.test/y"]
    )
    assert linked == (
        "값 [[1]](https://a.test/x)\n```\narr[1]\n```\n끝 [[1]](https://a.test/x)[[2]](https://b.test/y)"
    )
    assert cited == [1, 2]


def test_an_existing_link_is_not_linked_again() -> None:
    text = "[[1]](https://a.test/x) 와 [제목](https://a.test/x)"
    assert agent._link_citations(text, ["https://a.test/x"]) == (text, [])


def test_the_prompt_asks_for_numbers_not_urls() -> None:
    from app.models.chat import SessionKind
    from app.services import context

    prompt = context.system_prompt(SessionKind.chat, with_tools=True, web_search=True)
    assert "URL 은 옮겨 적지" in prompt
    assert "번호를 보고 시스템이 주소를 붙입니다" in prompt


@pytest.mark.asyncio
async def test_a_copied_title_does_not_invent_a_citation(monkeypatch) -> None:
    global _SEARCHES
    _SEARCHES = iter(
        [
            "'검색' 검색 결과:\n\n[1] 하나증권, ‘하나증권V’ 출격…AI 브리핑 탑재 - 디지털타임스\n"
            "https://www.dt.co.kr/a/1\n요약\n본문 발췌:\n다른 곳 https://ad.example.com/x\n\n"
            "[2] 짧은제목\nhttps://www.dt.co.kr/a/2\n요약\n"
        ]
    )
    answer = [
        _content("1. **하나증권, ‘하나증권V’ 출격…AI 브리핑 탑재**\n   새 MTS를 냈다.\n"),
        "data: [DONE]",
    ]
    _, events = await _collect(monkeypatch, [_calls("web_search", {"query": "검색"}), answer])
    text = _final_text(events)
    assert "탑재**\n" in text
    assert "[[1]]" not in text
    assert "### 출처" not in text
    assert "ad.example.com" not in text


@pytest.mark.asyncio
async def test_uncited_hits_and_page_links_are_not_automatically_endorsed(monkeypatch) -> None:
    global _SEARCHES
    _SEARCHES = iter(
        [
            "'검색' 검색 결과:\n\n[1] 결과\nhttps://www.suwon.go.kr/index.do\n요약\n\n"
            "[2] 기사 하나\nhttps://news.example.com/a/1\n요약\n본문 발췌:\n"
            "https://ar.wikipedia.org/wiki/2027 https://ary.wikipedia.org/wiki/2027\n"
        ]
    )
    answer = [_content("답입니다."), "data: [DONE]"]
    _, events = await _collect(monkeypatch, [_calls("web_search", {"query": "검색"}), answer])
    text = _final_text(events)
    assert "### 확인한 출처" not in text
    assert "news.example.com" not in text
    assert "wikipedia" not in text
    assert "suwon" not in text


@pytest.mark.asyncio
async def test_a_failed_searchs_own_error_text_is_never_a_source(monkeypatch) -> None:
    """A search that fails outright leaves nothing to list — not even the URL its
    own error message happens to mention (httpx's default text links to MDN's
    docs on the status code, which nobody searched for)."""

    async def run_failing_search(_args):
        return ToolResult(
            content=(
                "오류: 검색에 실패했습니다 (Client error '404 Not Found' for url "
                "'http://localhost:8100/tools/search/search?q=x'\n"
                "For more information check: "
                "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404)."
            ),
            failed=True,
        )

    tools = [
        Tool(
            name="web_search",
            description="웹 검색",
            parameters={"type": "object"},
            run=run_failing_search,
            label="웹 검색",
        ),
    ]
    answer = [_content("검색 없이 답합니다."), "data: [DONE]"]
    _, events = await _collect(
        monkeypatch, [_calls("web_search", {"query": "검색"}), answer], tools
    )
    text = _final_text(events)
    assert "### 확인한 출처" not in text
    assert "localhost" not in text
    assert "developer.mozilla.org" not in text
