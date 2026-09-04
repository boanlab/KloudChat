"""Model labels and vendor grouping: route prefixes are not vendors."""

from __future__ import annotations

from app.services.models import _label, _vendor


def test_a_route_prefix_groups_under_the_real_vendor() -> None:
    assert _vendor("local/gemma-4-26b-a4b-it", "hosted_vllm") == "Google"
    assert _vendor("strict-local/qwen3.6-35b", "hosted_vllm") == "Qwen"
    assert _vendor("qwen/qwen3.6-35b", "openrouter") == "Qwen"


def test_the_same_weights_over_two_routes_are_two_different_rows() -> None:
    plain = _label("google/gemma-4-26b-a4b-it")
    assert _label("local/gemma-4-26b-a4b-it") == f"{plain} (local)"
    assert _label("strict-local/gemma-4-26b-a4b-it") == f"{plain} (strict-local)"
    assert "(" not in plain


def test_a_vendor_prefix_is_not_a_route() -> None:
    assert _label("anthropic/claude-opus-4.8") == "Claude Opus 4.8"
    assert _label("openrouter/some-model:free") == "Some Model"
