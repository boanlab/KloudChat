"""Which model names titles and extracts memories.

`title_model` names a model, and a name is a claim about the deployment: a
`local/*` id only resolves where that GPU deployment exists. Both uses are best
effort, so a stale name produces no error — just no title, on every turn. These
tests pin the fallback that keeps that from happening silently.
"""

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
    previous = _seed(["local/glm-4.7-flash", "local/qwen3.6-35b"])
    monkeypatch.setattr(model_service.settings, "title_model", "local/glm-4.7-flash")
    try:
        assert await model_service.resolve_enrichment_model() == "local/glm-4.7-flash"
    finally:
        model_service._CACHE.update(previous)


@pytest.mark.asyncio
async def test_a_name_the_gateway_does_not_serve_falls_back(monkeypatch):
    # The GPU-less case: no local/* alias is generated, so the configured name
    # resolves to nothing. Returning "" hands the caller the session's own model.
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
    # Documented as "falls back to the session's own model", and it must not
    # cost a catalogue fetch to say so.
    def unreachable():  # pragma: no cover - the point is that it is not called
        raise AssertionError("list_models must not be consulted for an empty setting")

    monkeypatch.setattr(model_service.settings, "title_model", "")
    monkeypatch.setattr(model_service, "list_models", unreachable)
    assert await model_service.resolve_enrichment_model() == ""
