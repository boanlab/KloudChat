"""Each post-writing mechanism decides by the writer's model group (docs/architecture.md,
"Mechanisms by model group"): self-hosted (27B, flash), small external, strong external.

Round 8 ran 48 tasks on seven setups; the cases here use its own strings. Self-hosted writers
are `local/qwen3.8-27b` and `local/qwen3.8-flash`, a strong external one DeepSeek V4 Pro, a
small external one GLM-5.3 Flash."""

from __future__ import annotations

import pytest

from app.pipeline.bundle import Request
from app.services import model_tier

L27, LFL = "local/qwen3.8-27b", "local/qwen3.8-flash"
SMALL_EXT, STRONG_EXT = "z-ai/glm-5.3-flash", "deepseek/deepseek-v4-pro-0813"
#: a self-hosted writer and a strong external one: the two groups every change is tested on
GROUPS = pytest.mark.parametrize("writer", [L27, STRONG_EXT])


def test_the_groups(monkeypatch):
    monkeypatch.delenv("KCHAT_LIGHT_WRITERS", raising=False)
    assert model_tier.group(L27) == model_tier.group(LFL) == model_tier.SELF_HOSTED
    assert model_tier.group("strict-local/qwen3.8-27b") == model_tier.SELF_HOSTED
    assert model_tier.group("") == model_tier.SELF_HOSTED
    assert model_tier.group(SMALL_EXT) == model_tier.SMALL_EXTERNAL
    assert model_tier.group("deepseek/deepseek-v4.1-flash") == model_tier.SMALL_EXTERNAL
    assert model_tier.group(STRONG_EXT) == model_tier.STRONG_EXTERNAL
    assert model_tier.group("anthropic/claude-x") == model_tier.STRONG_EXTERNAL
    # the light-setup switch changes the prompt, not the group
    monkeypatch.setenv("KCHAT_LIGHT_WRITERS", L27)
    assert model_tier.strong(L27) and model_tier.group(L27) == model_tier.SELF_HOSTED
    # the model that actually wrote decides (a privacy route's local twin is self-hosted)
    assert Request(text="", model=STRONG_EXT, api_key="", actual_model=L27).group \
        == model_tier.SELF_HOSTED
