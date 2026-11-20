"""The `local/` and `strict-local/` twins of a Qwen3.8 model take pictures alike; what is sent is
still the privacy guard's decision (`reads_pictures`)."""

from __future__ import annotations

import pytest

from app.services import models
from app.services.workspace_context import reads_pictures


def _entry(model_id: str, *, strict: bool) -> dict:
    return {
        "model_name": model_id,
        "model_info": {
            "mode": "chat",
            "litellm_provider": "hosted_vllm",
            "supports_function_calling": True,
            "max_input_tokens": 131072,
            "kchat_data_boundary": "self_hosted" if strict else "hybrid",
            **({"kchat_strict_local": True} if strict else {}),
        },
    }


@pytest.mark.parametrize("name", ["qwen3.8-27b", "qwen3.8-flash"])
def test_a_twin_declares_vision_whichever_column_it_is_in(name: str) -> None:
    local = models._shape(_entry(f"local/{name}", strict=False))
    strict = models._shape(_entry(f"strict-local/{name}", strict=True))
    assert local is not None and strict is not None
    assert local["supportsVision"] is True and strict["supportsVision"] is True


def test_a_model_that_does_not_declare_vision_still_does_not() -> None:
    coder = models._shape(_entry("local/qwen3-coder-next", strict=False))
    assert coder is not None and coder["supportsVision"] is False


def test_only_the_strict_twin_is_sent_the_attached_pictures() -> None:
    local = models._shape(_entry("local/qwen3.8-flash", strict=False))
    strict = models._shape(_entry("strict-local/qwen3.8-flash", strict=True))
    assert reads_pictures(strict)
    assert not reads_pictures(local)
