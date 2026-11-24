"""Titles and memory run on the Super Agent's internal helper pick, never an outside model."""

from __future__ import annotations

import time

import pytest

from app.routers import sessions
from app.services import governance, super_agent
from app.services import models as model_service


def _seed(catalogue_ids: list[str]) -> dict:
    previous = dict(model_service._CACHE)
    model_service._CACHE.update(
        at=time.monotonic(),
        value={
            "models": [{"id": model_id, "kinds": ["chat"]} for model_id in catalogue_ids],
            "litellmAvailable": True,
            "defaultChatModel": "",
        },
    )
    return previous


async def _choose(monkeypatch, catalogue_ids, *, strict, writer_id, rules=None):
    async def policy(*_args, **_kwargs):
        return type("Policy", (), {"super_agent_rules": rules})()

    monkeypatch.setattr(governance, "current", policy)
    previous = _seed(catalogue_ids)
    try:
        return await sessions._enrichment_model(
            {"id": writer_id, "kinds": ["chat"]}, strict_local=strict, disable_fallbacks=False)
    finally:
        model_service._CACHE.update(previous)


def test_helpers_are_internal_whatever_the_table_says():
    rules = super_agent.normalise_rules({"simple": {
        "model": "deepseek/deepseek-v4.1-flash", "strictModel": "strict-local/qwen3.8-27b"}})
    assert super_agent.helper_models(rules, strict=False)[0] == "local/qwen3.8-27b"
    assert super_agent.helper_models(rules, strict=True)[0] == "strict-local/qwen3.8-27b"
    for strict in (False, True):
        assert all(
            m.startswith("strict-local/" if strict else "local/")
            for m in super_agent.helper_models(rules, strict=strict))


@pytest.mark.asyncio
async def test_titles_use_the_simple_rows_internal_model(monkeypatch):
    chosen = await _choose(
        monkeypatch, ["local/qwen3.8-flash", "local/qwen3.8-27b"],
        strict=False, writer_id="deepseek/deepseek-v4.1-flash")
    assert chosen["id"] == "local/qwen3.8-flash"


@pytest.mark.asyncio
async def test_a_strict_local_conversation_uses_the_strict_local_twin(monkeypatch):
    chosen = await _choose(
        monkeypatch,
        ["local/qwen3.8-flash", "strict-local/qwen3.8-flash", "strict-local/qwen3.8-27b"],
        strict=True, writer_id="strict-local/qwen3.8-27b")
    assert chosen["id"] == "strict-local/qwen3.8-flash"


@pytest.mark.asyncio
async def test_a_pick_the_gateway_does_not_serve_moves_to_the_next(monkeypatch):
    chosen = await _choose(
        monkeypatch, ["strict-local/qwen3.8-27b"], strict=True,
        writer_id="strict-local/other")
    assert chosen["id"] == "strict-local/qwen3.8-27b"


@pytest.mark.asyncio
async def test_nothing_served_leaves_the_writer(monkeypatch):
    chosen = await _choose(
        monkeypatch, [], strict=True, writer_id="strict-local/other")
    assert chosen["id"] == "strict-local/other"
