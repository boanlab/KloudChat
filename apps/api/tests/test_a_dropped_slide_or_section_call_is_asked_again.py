"""One retry of slide, section, page and chat calls on a dropped connection."""

from __future__ import annotations

import httpx
import pytest

from app.pipeline import report_stages
from app.services import agent, deck, model_retry, page
from app.services.chat import ChatStreamError
from app.services.tools.base import ToolContext

#: the drop as httpx raised it in the runs
PEER_CLOSED = httpx.RemoteProtocolError(
    "peer closed connection without sending complete message body (incomplete chunked read)")


def _flaky(failures: list[BaseException], answer=("슬라이드", {"inputTokens": 1,
                                                             "outputTokens": 1})):
    calls: list[tuple] = []

    async def call(*args, **kwargs):
        calls.append(args)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return answer

    return call, calls


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(model_retry, "PAUSE", 0)


@pytest.mark.asyncio
async def test_a_slide_call_dropped_once_is_asked_again(monkeypatch):
    call, calls = _flaky([PEER_CLOSED])
    monkeypatch.setattr(deck, "_complete_once", call)
    assert await deck._complete("m", [{"role": "user", "content": "x"}], "k", 600) == (
        "슬라이드", {"inputTokens": 1, "outputTokens": 1})
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_second_drop_or_a_timeout_or_a_refusal_is_not_hidden(monkeypatch):
    call, calls = _flaky([PEER_CLOSED, PEER_CLOSED])
    monkeypatch.setattr(deck, "_complete_once", call)
    with pytest.raises(httpx.RemoteProtocolError):
        await deck._complete("m", [], "k", 600)
    assert len(calls) == 2
    # a timeout already took the whole budget
    call, calls = _flaky([httpx.ReadTimeout("timed out")])
    monkeypatch.setattr(page, "_complete_once", call)
    with pytest.raises(httpx.ReadTimeout):
        await page._complete("m", [], "k", 600)
    assert len(calls) == 1
    # a request the gateway refused is the caller's to handle
    refused = httpx.HTTPStatusError(
        "400", request=httpx.Request("POST", "http://x"),
        response=httpx.Response(400, request=httpx.Request("POST", "http://x")))
    call, calls = _flaky([refused])
    monkeypatch.setattr(deck, "_complete_once", call)
    with pytest.raises(httpx.HTTPStatusError):
        await deck._complete("m", [], "k", 600)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_report_section_dropped_once_is_written_on_the_second_try(monkeypatch):
    from app.services import report

    gateway = httpx.HTTPStatusError(
        "502", request=httpx.Request("POST", "http://x"),
        response=httpx.Response(502, request=httpx.Request("POST", "http://x")))
    call, calls = _flaky([gateway], ("## 개요\n본문", {"inputTokens": 1, "outputTokens": 1}))
    monkeypatch.setattr(report, "_complete", call)
    text, _ = await report_stages._written("m", [], "k", max_tokens=1200)
    assert text == "## 개요\n본문" and len(calls) == 2


@pytest.mark.parametrize("status", [502, 503, 504])
@pytest.mark.asyncio
async def test_a_chat_hop_the_gateway_turned_away_is_asked_again(monkeypatch, status):
    seen: list[dict] = []

    async def stream(_model, _messages, _tools, *_args, **kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise ChatStreamError(f"upstream_{status}: no healthy upstream")
        acc = agent._Accumulator()
        acc.content.append("답입니다.")
        acc.finish_reason = "stop"
        yield "delta", "답입니다."
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(agent, "_DROP_RETRY_AFTER", 0)
    events = [e async for e in agent.run_turn(
        "local/qwen3.8-flash", [{"role": "user", "content": "릴리스 노트 써 줘"}], [],
        ToolContext(user_id="u", session_id="s"))]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "답입니다."
    assert len(seen) == 2
