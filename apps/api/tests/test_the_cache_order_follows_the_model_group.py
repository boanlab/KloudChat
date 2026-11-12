"""Who gets the cache-ordered system prompt: external models always; self-hosted ones only
when their server caches prefixes (`self_hosted_prompt_cache_order`)."""

from __future__ import annotations

from app.core.config import settings
from app.services import model_tier


def test_which_models_get_the_cache_order(monkeypatch):
    assert model_tier.cache_ordered("deepseek/deepseek-v4.1-flash")
    assert model_tier.cache_ordered("z-ai/glm-5.3")
    monkeypatch.setattr(settings, "self_hosted_prompt_cache_order", False)
    assert not model_tier.cache_ordered("local/qwen3.8-27b")
    assert not model_tier.cache_ordered("strict-local/qwen3.8-flash")
    monkeypatch.setattr(settings, "self_hosted_prompt_cache_order", True)
    assert model_tier.cache_ordered("local/qwen3.8-27b")
