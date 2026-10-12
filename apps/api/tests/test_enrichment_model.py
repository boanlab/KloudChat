"""Resolution of the enrichment model named by `title_model`."""

from __future__ import annotations

import time

import pytest

from app.services import models as model_service


def _seed(catalogue_ids: list[str]) -> dict:
    previous = dict(model_service._CACHE)
    model_service._CACHE.update(
        at=time.monotonic(),
        value={
            "models": [
                {"id": model_id, "kinds": ["chat"]} for model_id in catalogue_ids
            ],
            "litellmAvailable": True,
            "defaultChatModel": "",
        },
    )
    return previous


@pytest.mark.asyncio
async def test_a_served_model_is_used_as_configured(monkeypatch):
    previous = _seed(["local/glm-4.7-flash", "local/qwen3.8-27b"])
    monkeypatch.setattr(model_service.settings, "title_model", "local/glm-4.7-flash")
    try:
        assert await model_service.resolve_enrichment_model() == "local/glm-4.7-flash"
    finally:
        model_service._CACHE.update(previous)


@pytest.mark.asyncio
async def test_a_name_the_gateway_does_not_serve_falls_back(monkeypatch):
    # "" hands the caller the session's own model.
    previous = _seed(["z-ai/glm-4.7-flash", "qwen/qwen3.6-35b-a3b"])
    monkeypatch.setattr(model_service.settings, "title_model", "local/glm-4.7-flash")
    model_service._MISSING_ENRICHMENT.discard("local/glm-4.7-flash")
    try:
        assert await model_service.resolve_enrichment_model() == ""
    finally:
        model_service._CACHE.update(previous)
        model_service._MISSING_ENRICHMENT.discard("local/glm-4.7-flash")


@pytest.mark.asyncio
async def test_an_image_only_model_is_not_eligible(monkeypatch):
    previous = _seed([])
    model_service._CACHE["value"]["models"] = [
        {"id": "openai/gpt-5-image", "kinds": ["image"]}
    ]
    monkeypatch.setattr(model_service.settings, "title_model", "openai/gpt-5-image")
    model_service._MISSING_ENRICHMENT.discard("openai/gpt-5-image")
    try:
        assert await model_service.resolve_enrichment_model() == ""
    finally:
        model_service._CACHE.update(previous)
        model_service._MISSING_ENRICHMENT.discard("openai/gpt-5-image")


@pytest.mark.asyncio
async def test_an_empty_setting_needs_no_catalogue(monkeypatch):
    def unreachable():  # pragma: no cover - the point is that it is not called
        raise AssertionError("list_models must not be consulted for an empty setting")

    monkeypatch.setattr(model_service.settings, "title_model", "")
    monkeypatch.setattr(model_service, "list_models", unreachable)
    assert await model_service.resolve_enrichment_model() == ""


async def _strict_twin(monkeypatch, catalogue_ids, configured):
    from app.routers import sessions

    previous = _seed(catalogue_ids)
    monkeypatch.setattr(model_service.settings, "title_model", configured)
    model_service._MISSING_ENRICHMENT.discard(configured)
    writer = {"id": "strict-local/qwen3.8-27b", "kinds": ["chat"]}
    try:
        return await sessions._enrichment_model(writer, strict_local=True,
                                                disable_fallbacks=False)
    finally:
        model_service._CACHE.update(previous)


@pytest.mark.asyncio
async def test_a_strict_local_conversation_uses_the_chosen_models_strict_local_twin(monkeypatch):
    # As 시스템 › 모델 says: the chosen model's strict-local version does the side work.
    chosen = await _strict_twin(
        monkeypatch,
        ["local/qwen3.8-flash", "strict-local/qwen3.8-flash", "strict-local/qwen3.8-27b"],
        "local/qwen3.8-flash")
    assert chosen["id"] == "strict-local/qwen3.8-flash"


@pytest.mark.asyncio
async def test_without_a_strict_local_twin_the_conversations_model_does_it(monkeypatch):
    chosen = await _strict_twin(
        monkeypatch, ["local/qwen3.8-flash", "strict-local/qwen3.8-27b"], "local/qwen3.8-flash")
    assert chosen["id"] == "strict-local/qwen3.8-27b"
