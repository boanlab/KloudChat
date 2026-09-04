"""Model facts LiteLLM `/model/info` does not report: adapter-only models and overrides.

`ADAPTER_MODELS` lists models LiteLLM does not proxy (video); `MODEL_OVERRIDES`
corrects rows it proxies but describes incompletely (no `mode`, context window,
capability flags, or a zero price). Zero-priced remote models are dropped from
the catalogue by `services/models.py`.
"""

from __future__ import annotations

from typing import Any

#: Video models, called only via `videogen.submit`. Full per-second prices are
#: in `videogen._RATES`; `credit_cost` is the cheapest per-second rate.
ADAPTER_MODELS: list[dict[str, Any]] = [
    {
        "id": "google/veo-3.1-lite",
        "label": "Veo 3.1 Lite",
        "provider": "openrouter",
        "modality": "video",
        "kinds": ["av"],
        "credit_cost": 3_000,
        "adapter": "openrouter-video",
        "description": "Veo 3.1 경량판. 이 목록에서 가장 저렴합니다.",
    },
    {
        "id": "google/veo-3.1-fast",
        "label": "Veo 3.1 Fast",
        "provider": "openrouter",
        "modality": "video",
        "kinds": ["av"],
        "credit_cost": 8_000,
        "adapter": "openrouter-video",
        "description": "Veo 3.1 고속판.",
    },
    {
        "id": "google/veo-3.1",
        "label": "Veo 3.1",
        "provider": "openrouter",
        "modality": "video",
        "kinds": ["av"],
        "credit_cost": 20_000,
        "adapter": "openrouter-video",
        "description": "Veo 3.1 기본판.",
    },
    {
        "id": "openai/sora-2-pro",
        "label": "Sora 2 Pro",
        "provider": "openrouter",
        "modality": "video",
        "kinds": ["av"],
        "credit_cost": 30_000,
        "adapter": "openrouter-video",
        "description": "Sora 2. 소리가 항상 포함되어 무음 옵션이 없습니다.",
    },
]

# Every entry must also appear in `videogen.ALIASES` to be callable.


# Keyed on the reported `model_name`, merged over the `/model/info` row.
# Image `credit_cost` is per generated image (1 credit = $0.00001); unpriced
# models drop out of the catalogue and `GET /admin/settings` reports them.
MODEL_OVERRIDES: dict[str, dict[str, Any]] = {
    "nano-banana": {
        "label": "Nano Banana",
        "modality": "image",
        "kinds": ["image"],
        "description": "Gemini 이미지 생성. 빠르고 저렴한 기본값",
    },
    "nano-banana-2": {
        "label": "Nano Banana 2",
        "modality": "image",
        "kinds": ["image"],
        "description": "Gemini 이미지 생성 상위 모델. 디테일이 필요할 때",
    },
    "gpt-image-2": {
        "label": "GPT Image 2",
        "modality": "image",
        "kinds": ["image"],
        "description": "OpenAI 이미지 생성. 텍스트 렌더링에 강함",
    },
    # `supports_vision` decides whether an attached picture is sent at all; an
    # unlisted model is treated as text-only. Only contained models are listed:
    # the privacy guard reads text and cannot inspect an image. See
    # `workspace_context.reads_pictures`.
    "strict-local/qwen3.6-35b": {"supports_vision": True},
}

# Providers whose zero price is real (own hardware); a remote zero means unknown.
FREE_PROVIDERS = {"hosted_vllm", "vllm", "local", "openai_like"}

# Loopback and private ranges; a dotless host is a docker service name.
INTERNAL_HOST_PREFIXES = ("localhost", "127.", "10.", "192.168.", "172.", "host.docker.internal")


def is_internal_api_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    host = api_base.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if not host:
        return False
    if host.startswith(INTERNAL_HOST_PREFIXES):
        return True
    return "." not in host
