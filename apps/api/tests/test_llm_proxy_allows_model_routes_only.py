"""The public /llm proxy reaches LiteLLM's model endpoints only, never its admin surface."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import llm


@pytest.fixture
def client(monkeypatch):
    async def config():
        return "http://litellm.invalid", ""

    monkeypatch.setattr(llm.settings_store, "litellm_config", config)
    app = FastAPI()
    app.include_router(llm.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/llm/ui/"),
        ("GET", "/llm/ui/login"),
        ("GET", "/llm/openapi.json"),
        ("GET", "/llm/key/list"),
        ("POST", "/llm/key/generate"),
        ("GET", "/llm/user/list"),
        ("POST", "/llm/user/new"),
        ("GET", "/llm/spend/logs"),
        ("GET", "/llm/global/spend"),
        ("GET", "/llm/model/info"),
        ("POST", "/llm/model/new"),
        ("POST", "/llm/orvideo/v1/videos"),
        ("GET", "/llm/health/readiness"),
        ("PATCH", "/llm/v1/chat/completions"),
        ("GET", "/llm/v1/models/../key/list"),
    ],
)
def test_admin_and_pass_through_routes_are_not_found(client, method, path):
    assert client.request(method, path).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "v1/chat/completions",
        "chat/completions",
        "v1/responses",
        "v1/responses/resp_abc/input_items",
        "v1/messages",
        "v1/messages/count_tokens",
        "v1/embeddings",
        "v1/rerank",
        "rerank",
        "v1/audio/transcriptions",
        "v1/models",
        "v1/models/local/qwen3.8-27b",
    ],
)
def test_model_routes_are_allowed(path):
    assert llm._ALLOWED.fullmatch(path)


@pytest.mark.parametrize(
    "path", ["v1/models/../key/list", "v1/models/a/../../user/list", "v1/models//x"]
)
def test_dot_segments_never_pass(client, path):
    """httpx would normalise these upstream, so they are refused before the regex."""
    from starlette.requests import Request

    scope = {
        "type": "http", "method": "GET", "path": f"/llm/{path}", "headers": [], "query_string": b"",
    }
    import asyncio

    with pytest.raises(llm.HTTPException) as caught:
        asyncio.run(llm.proxy(path, Request(scope)))
    assert caught.value.status_code == 404
