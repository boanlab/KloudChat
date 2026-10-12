"""Model facts missing from LiteLLM `/model/info`: adapter-only models and overrides."""

from __future__ import annotations

from typing import Any

#: Video models via `videogen.submit` only; `credit_cost` = cheapest per-second rate.
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

# each entry must also be in `videogen.ALIASES`


# keyed on `model_name`, merged over `/model/info`; image credit_cost per image
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
    # vision only for contained models: privacy guard can't inspect images
    "strict-local/qwen3.8-27b": {"supports_vision": True},
    "strict-local/qwen3.8-flash": {"supports_vision": True},
}

# own-hardware providers: zero price is real
FREE_PROVIDERS = {"hosted_vllm", "vllm", "local", "openai_like"}

# loopback/private ranges; dotless host = docker service
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
