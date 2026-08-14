"""Model facts kchat holds that LiteLLM does not report.

Two tables:

* `ADAPTER_MODELS` — models LiteLLM does not proxy at all (video, most audio).
* `MODEL_OVERRIDES` — models LiteLLM proxies but describes badly.

Commercial models routed through OpenRouter come back from `/model/info` with
no `mode`, no context window, no capability flags, and `output_cost_per_token:
0` for image models — which at face value lists a paid model at zero credits.
Anything whose real modality or price LiteLLM cannot state is declared here.

Zero-priced models not served from our own GPUs are dropped from the catalogue
rather than offered as free. See `services/models.py`.
"""


from __future__ import annotations

from typing import Any

#: The video-only registry.
#:
#: Images and audio go over `/v1/chat/completions`, which `/model/info`
#: describes. Video is a pass-through to `/api/v1/videos`, which it does not —
#: so without an entry here the picker shows nothing.
#:
#: Only models `videogen.submit` can call belong here. Prices vary by
#: (resolution × audio × duration) and live in `videogen._RATES`, the same table
#: the proxy is billed against. `credit_cost` is the cheapest per-second rate,
#: for places with room for one number.
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

# Declaring a model here does not make it callable: every entry is checked
# against `videogen.ALIASES`, which decides whether a priced path exists.


# ── Corrections for models LiteLLM describes incorrectly ───────────────────
#
# Keyed on the reported `model_name` and merged over the `/model/info` row, so
# only the wrong fields need listing.
#
# For image models `credit_cost` is per generated image, not per token; the unit
# is the usual one (1 credit = $0.00001).
#
# OpenRouter publishes no per-image price, so those are left empty rather than
# estimated. An unpriced model drops out of the list and `GET /admin/settings`
# reports it.
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
}

# Providers whose zero price is real: our own hardware. A remote model
# reporting zero means the price is unknown.
FREE_PROVIDERS = {"hosted_vllm", "vllm", "local", "openai_like"}

# `api_base` hosts that mean our own infrastructure: docker service names have
# no dot, and loopback and private ranges are never a commercial endpoint.
INTERNAL_HOST_PREFIXES = ("localhost", "127.", "10.", "192.168.", "172.", "host.docker.internal")


def is_internal_api_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    host = api_base.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if not host:
        return False
    if host.startswith(INTERNAL_HOST_PREFIXES):
        return True
    # No dot at all → a container/service name on our own network.
    return "." not in host
