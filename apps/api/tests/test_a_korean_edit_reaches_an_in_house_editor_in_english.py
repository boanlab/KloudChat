"""An in-house editing model follows a Korean whole-picture edit (a style, brightness) far less
than the same words in English: the instruction is rewritten first, quoted words kept."""

import json

import httpx
import pytest

from app.services import imagegen, settings_store


def _answer(monkeypatch, text, sent):
    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    async def config():
        return "http://llm", "k"

    real = httpx.AsyncClient
    monkeypatch.setattr(settings_store, "litellm_config", config)
    monkeypatch.setattr(
        imagegen.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(reply), **kw),
    )


@pytest.mark.asyncio
async def test_a_korean_instruction_goes_in_english(monkeypatch):
    sent: list[dict] = []
    _answer(monkeypatch, "Redraw the whole image as a watercolor painting.", sent)
    out = await imagegen.plan_edit("수채화 스타일로 바꿔줘", model="m", api_key="k")
    assert out == "Redraw the whole image as a watercolor painting."
    assert "수채화 스타일로 바꿔줘" in sent[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_an_english_instruction_is_not_sent_anywhere(monkeypatch):
    sent: list[dict] = []
    _answer(monkeypatch, "unused", sent)
    out = await imagegen.plan_edit("make it brighter", model="m", api_key="k")
    assert out == "make it brighter"
    assert sent == []


@pytest.mark.asyncio
async def test_a_lost_quoted_word_gives_the_request_as_typed(monkeypatch):
    sent: list[dict] = []
    _answer(monkeypatch, "Change the sign text to closed.", sent)
    request = "간판의 글자를 'CLOSED'로 바꿔줘"
    assert await imagegen.plan_edit(request, model="m", api_key="k") == request


@pytest.mark.asyncio
async def test_a_kept_quoted_word_stays_in_the_instruction(monkeypatch):
    sent: list[dict] = []
    _answer(monkeypatch, "Change the sign text to 'CLOSED'.", sent)
    out = await imagegen.plan_edit("간판의 글자를 'CLOSED'로 바꿔줘", model="m", api_key="k")
    assert out == "Change the sign text to 'CLOSED'."


@pytest.mark.asyncio
async def test_a_failed_planner_gives_the_request_as_typed(monkeypatch):
    async def config():
        return "http://llm", "k"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    real = httpx.AsyncClient
    monkeypatch.setattr(settings_store, "litellm_config", config)
    monkeypatch.setattr(
        imagegen.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(boom), **kw),
    )
    assert await imagegen.plan_edit("더 밝게 해줘", model="m", api_key="k") == "더 밝게 해줘"
