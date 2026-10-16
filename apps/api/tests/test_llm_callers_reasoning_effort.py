"""/llm 호출자의 추론 단계: 끌 수 없는 모델에 「끄기」를 보내면 가장 낮은 단계로 바꿔 보내고,
모델 목록에는 모델마다 받는 단계를 붙인다. 바꿀 것이 없는 요청은 바이트 그대로 간다."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import llm
from app.services import models as model_service
from app.services import reasoning

GLM = {"efforts": ["low", "high", "max"], "default": "max", "mandatory": True}
DEEPSEEK = {"efforts": ["none", "low", "high", "max"], "default": "high", "mandatory": False}
CATALOGUE = {"models": [
    {"id": "z-ai/glm-5.3-flash", "reasoning": GLM},
    {"id": "deepseek/deepseek-v4.1-flash", "reasoning": DEEPSEEK},
    {"id": "local/qwen3.8-27b", "reasoning": None},
]}


@pytest.fixture
def upstream(monkeypatch):
    """The proxy's LiteLLM, answering from `seen`; records each request body."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = {"ok": True}
        if request.url.path.endswith("/models"):
            payload = {"object": "list", "data": [
                {"id": "z-ai/glm-5.3-flash", "object": "model"},
                {"id": "local/qwen3.8-27b", "object": "model"},
            ]}
        # a stream, as LiteLLM's: the proxy relays raw chunks
        return httpx.Response(200, headers={"content-type": "application/json"},
                              stream=httpx.ByteStream(json.dumps(payload).encode()))

    real = httpx.AsyncClient

    async def config():
        return "http://litellm.invalid", ""

    async def catalogue(force: bool = False):
        return CATALOGUE

    monkeypatch.setattr(llm.settings_store, "litellm_config", config)
    monkeypatch.setattr(model_service, "list_models", catalogue)
    monkeypatch.setattr(
        llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw)
    )
    app = FastAPI()
    app.include_router(llm.router)
    return TestClient(app), seen


@pytest.mark.parametrize(
    "path,sent,expected",
    [
        ("v1/chat/completions", {"reasoning_effort": "none"}, {"reasoning_effort": "low"}),
        ("v1/chat/completions", {"reasoning": {"enabled": False}},
         {"reasoning": {"effort": "low"}}),
        ("v1/responses", {"reasoning": {"effort": "none", "summary": "auto"}},
         {"reasoning": {"effort": "low", "summary": "auto"}}),
        ("v1/messages", {"thinking": {"type": "disabled"}}, {}),
    ],
)
def test_no_reasoning_on_a_model_that_must_reason_becomes_the_least(upstream, path, sent, expected):
    client, seen = upstream
    body = {"model": "z-ai/glm-5.3-flash", "messages": [], **sent}
    assert client.post(f"/llm/{path}", json=body).status_code == 200
    forwarded = json.loads(seen[-1].content)
    assert forwarded == {"model": "z-ai/glm-5.3-flash", "messages": [], **expected}


@pytest.mark.parametrize(
    "body",
    [
        # the model may go without: none is its own level
        {"model": "deepseek/deepseek-v4.1-flash", "reasoning_effort": "none"},
        # a level it takes, or one OpenRouter maps to a near one
        {"model": "z-ai/glm-5.3-flash", "reasoning_effort": "medium"},
        {"model": "z-ai/glm-5.3-flash", "reasoning": {"effort": "high"}},
        {"model": "local/qwen3.8-27b", "reasoning_effort": "none"},
        {"model": "unknown/model", "reasoning_effort": "none"},
    ],
)
def test_anything_else_goes_byte_for_byte(upstream, body):
    client, seen = upstream
    raw = json.dumps(body, separators=(",", ":")).encode()
    assert client.post("/llm/v1/chat/completions", content=raw).status_code == 200
    assert seen[-1].content == raw


def test_the_model_listing_says_which_levels_each_model_takes(upstream):
    client, _ = upstream
    listed = client.get("/llm/v1/models").json()["data"]
    assert listed == [
        {"id": "z-ai/glm-5.3-flash", "object": "model", "reasoning": GLM},
        {"id": "local/qwen3.8-27b", "object": "model", "reasoning": None},
    ]


def test_levels_come_from_model_info_ordered_least_first():
    info = {
        "supports_reasoning": True,
        "kchat_reasoning_efforts": ["max", "high", "low"],
        "kchat_reasoning_default": "max",
        "kchat_reasoning_mandatory": True,
    }
    assert reasoning.of(info) == {
        "efforts": ["low", "high", "max"], "default": "max", "mandatory": True,
    }
    assert reasoning.of({"kchat_reasoning_efforts": ["low"]}) is None
    assert reasoning.of({"supports_reasoning": True}) is None
    assert reasoning.least({"efforts": ["none", "minimal", "low"]}) == "minimal"


def test_the_catalogue_carries_the_levels_from_model_info():
    """The real shaping, not a stand-in: a /model/info row becomes a model with `reasoning`."""
    row = {
        "model_name": "z-ai/glm-5.3-flash",
        "litellm_params": {"model": "openrouter/z-ai/glm-5.3-flash:floor"},
        "model_info": {
            "mode": "chat", "litellm_provider": "openrouter",
            "input_cost_per_token": 1.5e-7, "output_cost_per_token": 5e-7,
            "supports_reasoning": True, "kchat_reasoning_efforts": ["max", "high", "low"],
            "kchat_reasoning_default": "max", "kchat_reasoning_mandatory": True,
        },
    }
    assert model_service._shape(row)["reasoning"] == GLM
    del row["model_info"]["supports_reasoning"]
    assert model_service._shape(row)["reasoning"] is None
