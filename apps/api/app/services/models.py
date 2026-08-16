"""The merged model catalogue the picker reads.

Three sources, in precedence order:

1. `MODEL_OVERRIDES` — facts LiteLLM does not carry.
2. LiteLLM `/model/info` — the live list and, where available, real pricing.
3. `ADAPTER_MODELS` — models LiteLLM does not proxy at all.

Also the only module that knows the USD→credit exchange rate.

**Fail closed on price.** A remote model priced at zero means "price unknown",
not "free"; it is dropped from the catalogue and recorded. `MODEL_OVERRIDES` is
how an operator brings one back.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import settings
from app.services import litellm
from app.services.adapters import (
    ADAPTER_MODELS,
    FREE_PROVIDERS,
    MODEL_OVERRIDES,
    is_internal_api_base,
)

log = logging.getLogger(__name__)

#: Output tokens per generated image; families differ by up to 4×. Measured
#: values for gemini-2.5-flash-image and gpt-5-image-mini, the rest inferred
#: from their family.
_IMAGE_TOKENS_DEFAULT = 5450
_IMAGE_TOKENS = {
    "google/gemini-2.5-flash-image": 1290,
    "google/gemini-3-pro-image": 1290,  # inferred: same family
    "openai/gpt-5-image-mini": 5450,
    "openai/gpt-5-image": 5450,  # inferred: same family
}

#: Models dropped from the list for want of a price, `{model_id: provider}`.
#: Rebuilt on every catalogue refresh and surfaced in the admin screen.
_unpriced: dict[str, str] = {}


def unpriced() -> dict[str, str]:
    """Models hidden for want of a price, newest catalogue first."""
    return dict(_unpriced)


# LiteLLM `mode` → (KloudChat modality, selectable surfaces). Anything unlisted is
# infrastructure and never reaches the picker.
_MODE_MAP: dict[str, tuple[str, list[str]]] = {
    "chat": ("chat", ["chat", "report", "slides"]),
    "completion": ("chat", ["chat", "report", "slides"]),
    "image_generation": ("image", ["image"]),
    "audio_speech": ("audio", ["av"]),
}

_KINDS_FOR: dict[str, list[str]] = {
    "chat": ["chat", "report", "slides"],
    "image": ["image"],
    "audio": ["av"],
    "video": ["av"],
}

# Tokens that should not be title-cased when building a display label.
_ACRONYMS = {"gpt", "ai", "llm", "xtts", "ltx", "tts", "stt", "hd", "sd", "vl", "glm"}

# Vendor display names. An id's first segment is a routing slug rather than a
# brand; anything absent falls back to a title-cased slug.
_VENDORS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "x-ai": "xAI",
    "perplexity": "Perplexity",
    "deepseek": "DeepSeek",
    "z-ai": "Z.ai",
    "tencent": "Tencent",
    "xiaomi": "Xiaomi",
    "moonshotai": "Moonshot",
    "qwen": "Qwen",
    "meta-llama": "Meta",
    "nvidia": "NVIDIA",
    "minimax": "MiniMax",
}

# `local/<name>` says where a model runs, not who built it. Mapped to the real
# vendor so the picker reads "Qwen · Qwen3.6 27b".
_LOCAL_VENDORS = (
    ("qwen", "Qwen"),
    ("glm", "Z.ai"),
    ("gemma", "Google"),
    ("llama", "Meta"),
    ("mistral", "Mistral"),
    ("deepseek", "DeepSeek"),
)


def _vendor(model_id: str, provider: str) -> str:
    """Company that built the model, for display next to its name."""
    head = model_id.split("/")[0] if "/" in model_id else ""
    if head and head != "local":
        return _VENDORS.get(head, head.replace("-", " ").title())
    tail = model_id.split("/")[-1].lower()
    for needle, name in _LOCAL_VENDORS:
        if needle in tail:
            return name
    # No id hint: fall back to the routing provider rather than inventing one.
    return _VENDORS.get(provider, (provider or "기타").replace("_", " ").title())


_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_CACHE_TTL_SEC = 30.0

_DATA_BOUNDARIES = {"self_hosted", "hybrid", "external"}


def _data_boundary(info: dict[str, Any]) -> tuple[str, bool, bool]:
    """Returns the proxy-declared data boundary and privacy flags.

    Model ids, providers and API-base heuristics are deliberately ignored: a
    ``local/*`` alias may still fall back to OpenRouter. Missing or malformed
    metadata is therefore ``unknown`` and never eligible as a safe route.
    """
    raw = info.get("kchat_data_boundary")
    boundary = raw if isinstance(raw, str) and raw in _DATA_BOUNDARIES else "unknown"
    strict = boundary == "self_hosted" and info.get("kchat_strict_local") is True
    privacy_only = strict and info.get("kchat_privacy_only") is True
    return boundary, strict, privacy_only


def _credits(usd: float) -> int:
    """USD → credits, rounded up so a priced model never shows as free."""
    if usd <= 0:
        return 0
    return max(1, round(usd * settings.credits_per_usd))


def _label(model_id: str) -> str:
    """`anthropic/claude-opus-4.8` → `Claude Opus 4.8`.

    The model name ALONE — the vendor is added by `_shape`. Keeping them separate
    means a caller that already groups by vendor is not forced to render it twice.

    Only a fallback — anything whose generated label reads badly belongs in
    `MODEL_OVERRIDES` with a hand-written one.
    """
    tail = model_id.split("/")[-1]
    # OpenRouter's free-tier suffix is a price, not a name — stripped from the
    # label, conveyed as a cost of 0.
    if tail.endswith(":free"):
        tail = tail[: -len(":free")]
    if not tail:
        return model_id
    out = []
    for word in tail.replace("_", "-").split("-"):
        if not word:
            continue
        if word.lower() in _ACRONYMS:
            out.append(word.upper())
        elif any(ch.isdigit() for ch in word):
            # Version-ish tokens keep their shape (4.8, v2, 122b) but still
            # take a leading capital when they start with a letter.
            out.append(word[0].upper() + word[1:] if word[0].isalpha() else word)
        else:
            out.append(word.capitalize())
    return " ".join(out) or model_id


def _shape(entry: dict[str, Any]) -> dict[str, Any] | None:
    """One `/model/info` row → a KloudChat `ModelInfo`, or None if it should not be listed."""
    model_id = entry.get("model_name")
    if not model_id:
        return None

    info = entry.get("model_info") or {}
    override = MODEL_OVERRIDES.get(model_id, {})

    # Routing-only deployments opt out: LiteLLM registers an OpenRouter twin of
    # each self-hosted model for failover, which is not a separate choice.
    if info.get("kchat_hidden"):
        return None

    # Modality: override, then LiteLLM's `mode`, then chat. The price check
    # below is what catches a misclassified paid model.
    if "modality" in override:
        modality = override["modality"]
    else:
        mode = (info.get("mode") or "").lower()
        mapped = _MODE_MAP.get(mode)
        if mode and mapped is None:
            return None  # embeddings, rerank, transcription — infrastructure
        modality = mapped[0] if mapped else "chat"

    kinds = override.get("kinds") or _KINDS_FOR.get(modality, ["chat"])
    provider = (
        override.get("provider")
        or info.get("litellm_provider")
        or entry.get("litellm_params", {}).get("custom_llm_provider")
        or (model_id.split("/")[0] if "/" in model_id else "unknown")
    )

    # Input priced and billed separately: counting output alone would make a
    # 100k-token context free, and that is where the money goes.
    input_credit_cost = 0
    if "input_credit_cost" in override:
        input_credit_cost = int(override["input_credit_cost"])
    elif modality == "chat":
        input_credit_cost = _credits(float(info.get("input_cost_per_token") or 0) * 1000)

    if "credit_cost" in override:
        credit_cost = int(override["credit_cost"])
    elif modality == "chat":
        # One number per 1k output tokens is the unit users can reason about.
        credit_cost = _credits(float(info.get("output_cost_per_token") or 0) * 1000)
    elif modality == "image":
        # Same unit as chat: OpenRouter's picture models charge output tokens,
        # and the turn is settled from reported token counts.
        # `output_cost_per_image` is zero for all of them.
        credit_cost = _credits(float(info.get("output_cost_per_image") or 0)) or _credits(
            float(info.get("output_cost_per_token") or 0) * 1000
        )
    else:
        # All three audio pricing shapes: per character (TTS), per output token
        # (GPT Audio), flat per clip (Lyria). Reading one leaves the others at
        # zero, and a zero-priced model is hidden. 900 characters ≈ one minute.
        credit_cost = (
            _credits(float(info.get("output_cost_per_character") or 0) * 900)
            or _credits(float(info.get("output_cost_per_token") or 0) * 1000)
            or _credits(float(info.get("output_cost_per_request") or 0))
        )

    # Zero is believed only where we serve: a known self-hosting provider, an
    # internal `api_base`, or an explicit override. OpenRouter's `:free` is a
    # stated price, so it counts too.
    self_hosted = (
        provider in FREE_PROVIDERS
        or model_id.endswith(":free")
        or is_internal_api_base(entry.get("litellm_params", {}).get("api_base"))
    )
    if credit_cost == 0 and not self_hosted and "credit_cost" not in override:
        # Recorded, not just logged — the admin screen reports what was dropped
        # and why.
        _unpriced[model_id] = provider
        log.warning(
            "model %r priced at 0 by provider %r — hidden from the catalogue. "
            "Add it to MODEL_OVERRIDES with a real credit_cost to list it.",
            model_id,
            provider,
        )
        return None

    context = (
        override.get("context_window")
        or info.get("max_input_tokens")
        or info.get("actual_ctx_tokens")
        or info.get("max_tokens")
    )
    supported = info.get("supported_openai_params") or []

    # `label` is "Vendor · Model": every surface reads this one field, and a
    # bare name does not say who is being billed. `name`/`vendor` stay separate
    # for callers that lay them out themselves.
    name = override.get("label") or _label(model_id)
    vendor = override.get("vendor") or _vendor(model_id, provider)
    data_boundary, strict_local, privacy_only = _data_boundary(info)
    return {
        "id": model_id,
        "label": f"{vendor} · {name}",
        "name": name,
        "vendor": vendor,
        "provider": provider,
        "dataBoundary": data_boundary,
        "strictLocal": strict_local,
        "privacyOnly": privacy_only,
        "modality": modality,
        "kinds": kinds,
        # Per-image and per-call prices. `creditCost` is per 1k output tokens
        # and cannot quote either; a flat per-request price wins because these
        # models emit almost no output tokens.
        "creditPerCall": _credits(float(info.get("output_cost_per_request") or 0)),
        "creditPerImage": (
            _credits(
                float(info.get("output_cost_per_token") or 0)
                * _IMAGE_TOKENS.get(model_id, _IMAGE_TOKENS_DEFAULT)
            )
            if modality == "image"
            else 0
        ),
        # Credits per second by (resolution, audio), read from the same table
        # the pass-through is billed against.
        "creditPerSecond": _video_rates(model_id) if modality == "video" else {},
        "creditCost": credit_cost,
        "inputCreditCost": input_credit_cost,
        "contextWindow": context,
        "supportsVision": bool(override.get("supports_vision", info.get("supports_vision"))),
        # `supported_openai_params` is the proxy's dialect list, not a per-model
        # capability — it lists `tools` even for image endpoints.
        "supportsTools": bool(
            override.get(
                "supports_tools",
                modality == "chat"
                and (info.get("supports_function_calling") or "tools" in supported),
            )
        ),
        "adapter": None,
        # The picker's price line already conveys free, and where from.
        "description": override.get("description", ""),
    }


def _adapter_entries() -> list[dict[str, Any]]:
    return [
        {
            "id": m["id"],
            # Same "Vendor · Model" shape as proxied rows; the picker mixes both.
            "label": f"{_vendor(m['id'], m['provider'])} · {m['label']}",
            "name": m["label"],
            "vendor": _vendor(m["id"], m["provider"]),
            "provider": m["provider"],
            "dataBoundary": "external",
            "strictLocal": False,
            "privacyOnly": False,
            "modality": m["modality"],
            "kinds": m["kinds"],
            "creditCost": m["credit_cost"],
            # Adapter models are billed per asset; there is no input rate.
            "inputCreditCost": 0,
            "contextWindow": None,
            "supportsVision": False,
            "supportsTools": False,
            "adapter": m["adapter"],
            "description": m["description"],
            "creditPerSecond": _video_rates(m["id"]),
            "creditPerCall": 0,
            "creditPerImage": 0,
        }
        for m in ADAPTER_MODELS
    ]


def _video_rates(model_id: str) -> dict[str, int]:
    """`{"720p:silent": 3000, …}` — credits per second, per shape of clip.

    Keys match what the composer builds from its controls. An unpriced
    combination is absent, and the submit endpoint refuses it.
    """
    from app.services import videogen

    alias = videogen.ALIASES.get(model_id)
    if alias is None:
        return {}
    return {
        f"{resolution}:{'sound' if audio else 'silent'}": _credits(rate)
        for (key, resolution, audio), rate in videogen._RATES.items()
        if key == alias
    }


_MODALITY_ORDER = {"chat": 0, "image": 1, "audio": 2, "video": 3}


def find(models: list[dict[str, Any]], model_id: str) -> dict[str, Any] | None:
    return next((m for m in models if m["id"] == model_id), None)


async def list_models(force: bool = False) -> dict[str, Any]:
    """Merged catalogue plus whether LiteLLM answered.

    `litellmAvailable: false` is a result, not an error: adapter models are
    still listed and the UI can distinguish "proxy down" from "empty list".
    """
    now = time.monotonic()
    if not force and _CACHE["value"] is not None and now - _CACHE["at"] < _CACHE_TTL_SEC:
        return _CACHE["value"]

    proxied: list[dict[str, Any]] = []
    available = True
    # Rebuilt from scratch — a model since priced must stop reading as hidden.
    _unpriced.clear()
    try:
        # `/model/info` returns one row per deployment, so a load-balanced model
        # arrives twice under one `model_name`. First row per id wins.
        proxied_ids: set[str] = set()
        for entry in await litellm.model_info():
            shaped = _shape(entry)
            if shaped and shaped["id"] not in proxied_ids:
                proxied_ids.add(shaped["id"])
                proxied.append(shaped)
    except litellm.LiteLLMError as exc:
        log.warning("model catalogue falling back to adapters only: %s", exc)
        available = False

    # LiteLLM first as the live source, adapters after, deduped by id.
    seen = {m["id"] for m in proxied}
    merged = proxied + [m for m in _adapter_entries() if m["id"] not in seen]
    merged.sort(key=lambda m: (_MODALITY_ORDER.get(m["modality"], 9), m["provider"], m["id"]))

    # Resolved here: "cheapest" in the UI would be decided by sort order, since
    # self-hosted models are priced at 0.
    default_chat = settings.default_chat_model
    if not any(m["id"] == default_chat and "chat" in m["kinds"] for m in merged):
        default_chat = ""

    result = {
        "models": merged,
        "litellmAvailable": available,
        "defaultChatModel": default_chat,
    }
    _CACHE.update(at=now, value=result)
    return result


async def list_models_for_egress() -> dict[str, Any]:
    """Returns a live catalogue suitable for privacy and routing decisions.

    The ordinary 30-second catalogue is a display/performance cache. A strict
    alias can be remapped to an external deployment during that window, so an
    outbound request must refresh `/model/info`. If the gateway cannot answer,
    an empty catalogue fails the turn closed instead of trusting either the
    stale cache or adapter entries for a privacy decision.
    """
    catalogue = await list_models(force=True)
    if catalogue.get("litellmAvailable") is not True:
        return {
            **catalogue,
            "models": [],
            "defaultChatModel": "",
        }
    return catalogue


def invalidate_cache() -> None:
    _CACHE.update(at=0.0, value=None)
