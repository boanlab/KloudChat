"""The merged model catalogue the picker reads.

Sources, in precedence order: `MODEL_OVERRIDES`, LiteLLM `/model/info`,
`ADAPTER_MODELS`. Also the only module that knows the USD→credit rate.

Fail closed on price: a remote model priced at zero is hidden and recorded in
`unpriced()`; `MODEL_OVERRIDES` brings it back.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import settings
from app.services import imagegen, litellm
from app.services.adapters import (
    ADAPTER_MODELS,
    FREE_PROVIDERS,
    MODEL_OVERRIDES,
    is_internal_api_base,
)

log = logging.getLogger(__name__)

#: Output tokens per generated image. Measured for gemini-2.5-flash-image and
#: gpt-5-image-mini; the rest inferred from their family.
_IMAGE_TOKENS_DEFAULT = 5450
_IMAGE_TOKENS = {
    "google/gemini-2.5-flash-image": 1290,
    "google/gemini-3-pro-image": 1290,
    "openai/gpt-5-image-mini": 5450,
    "openai/gpt-5-image": 5450,
}

#: Models hidden for want of a price, `{model_id: provider}`, rebuilt per refresh.
_unpriced: dict[str, str] = {}


def unpriced() -> dict[str, str]:
    """Models hidden for want of a price, from the latest catalogue build."""
    return dict(_unpriced)


#: LiteLLM `mode` → KloudChat modality. Unlisted modes are infrastructure.
_MODE_MAP: dict[str, str] = {
    "chat": "chat",
    "completion": "chat",
    "image_generation": "image",
    "audio_speech": "audio",
}

#: Modality → surfaces it can be selected on.
_KINDS_FOR: dict[str, list[str]] = {
    "chat": ["chat", "report", "slides"],
    "image": ["image"],
    "audio": ["av"],
    "video": ["av"],
}

#: Tokens kept upper-case in a generated label.
_ACRONYMS = {"gpt", "ai", "llm", "xtts", "ltx", "tts", "stt", "hd", "sd", "vl", "glm"}

#: Vendor display names by id prefix; absent ones fall back to a title-cased slug.
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

#: Vendor of a `local/<name>` model, by substring of the name.
_LOCAL_VENDORS = (
    ("qwen", "Qwen"),
    ("glm", "Z.ai"),
    ("gemma", "Google"),
    ("llama", "Meta"),
    ("mistral", "Mistral"),
    ("deepseek", "DeepSeek"),
)


#: Route prefixes: where a model runs, not who built it. Named on the label so
#: the same weights served two ways are distinguishable.
_ROUTES = ("local", "strict-local")


def _vendor(model_id: str, provider: str) -> str:
    """Company that built the model, for display next to its name."""
    head = model_id.split("/")[0] if "/" in model_id else ""
    if head and head not in _ROUTES:
        return _VENDORS.get(head, head.replace("-", " ").title())
    tail = model_id.split("/")[-1].lower()
    for needle, name in _LOCAL_VENDORS:
        if needle in tail:
            return name
    return _VENDORS.get(provider, (provider or "기타").replace("_", " ").title())


_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_CACHE_TTL_SEC = 30.0

_DATA_BOUNDARIES = {"self_hosted", "hybrid", "external"}


def _data_boundary(info: dict[str, Any]) -> tuple[str, bool, bool]:
    """`(boundary, strict_local, privacy_only)` from proxy-declared metadata only.

    Ids and API-base heuristics are ignored: a `local/*` alias may still fall
    back to an external provider. Missing metadata is `unknown`.
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
    """`anthropic/claude-opus-4.8` → `Claude Opus 4.8`; route prefixes are kept in brackets."""
    head, _, tail = model_id.rpartition("/")
    # OpenRouter's `:free` suffix is a price, conveyed as a cost of 0.
    if tail.endswith(":free"):
        tail = tail[: -len(":free")]
    if not tail:
        return model_id
    route = f" ({head})" if head in _ROUTES else ""
    out = []
    for word in tail.replace("_", "-").split("-"):
        if not word:
            continue
        if word.lower() in _ACRONYMS:
            out.append(word.upper())
        elif any(ch.isdigit() for ch in word):
            # Version-ish tokens keep their shape (4.8, v2, 122b).
            out.append(word[0].upper() + word[1:] if word[0].isalpha() else word)
        else:
            out.append(word.capitalize())
    return (" ".join(out) or model_id) + route


def _shape(entry: dict[str, Any]) -> dict[str, Any] | None:
    """One `/model/info` row → a KloudChat `ModelInfo`, or None if it should not be listed."""
    model_id = entry.get("model_name")
    if not model_id:
        return None

    info = entry.get("model_info") or {}
    override = MODEL_OVERRIDES.get(model_id, {})

    # Routing-only deployments (failover twins) opt out.
    if info.get("kchat_hidden"):
        return None

    # Modality: override, then LiteLLM's `mode`, then chat.
    if "modality" in override:
        modality = override["modality"]
    else:
        mode = (info.get("mode") or "").lower()
        mapped = _MODE_MAP.get(mode)
        if mode and mapped is None:
            return None
        modality = mapped if mapped else "chat"

    kinds = override.get("kinds") or _KINDS_FOR.get(modality, ["chat"])
    provider = (
        override.get("provider")
        or info.get("litellm_provider")
        or entry.get("litellm_params", {}).get("custom_llm_provider")
        or (model_id.split("/")[0] if "/" in model_id else "unknown")
    )

    # Input is priced and billed separately from output.
    input_credit_cost = 0
    if "input_credit_cost" in override:
        input_credit_cost = int(override["input_credit_cost"])
    elif modality == "chat":
        input_credit_cost = _credits(float(info.get("input_cost_per_token") or 0) * 1000)

    if "credit_cost" in override:
        credit_cost = int(override["credit_cost"])
    elif modality == "chat":
        # Credits per 1k output tokens.
        credit_cost = _credits(float(info.get("output_cost_per_token") or 0) * 1000)
    elif modality == "image":
        # Same unit as chat; picture models charge output tokens and
        # `output_cost_per_image` is zero for all of them.
        credit_cost = _credits(float(info.get("output_cost_per_image") or 0)) or _credits(
            float(info.get("output_cost_per_token") or 0) * 1000
        )
    else:
        # Audio pricing shapes: per character (TTS, 900 characters ≈ one
        # minute), per output token, flat per request.
        credit_cost = (
            _credits(float(info.get("output_cost_per_character") or 0) * 900)
            or _credits(float(info.get("output_cost_per_token") or 0) * 1000)
            or _credits(float(info.get("output_cost_per_request") or 0))
        )

    # Zero is believed only for self-hosting providers, internal `api_base`,
    # OpenRouter `:free`, or an explicit override.
    self_hosted = (
        provider in FREE_PROVIDERS
        or model_id.endswith(":free")
        or is_internal_api_base(entry.get("litellm_params", {}).get("api_base"))
    )
    if credit_cost == 0 and not self_hosted and "credit_cost" not in override:
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

    # `label` is "Vendor · Model"; `name`/`vendor` stay separate for callers
    # that lay them out themselves.
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
        # Per-call and per-image prices for models that emit almost no output tokens.
        "creditPerCall": _credits(float(info.get("output_cost_per_request") or 0)),
        "creditPerImage": (
            _credits(
                float(info.get("output_cost_per_token") or 0)
                * _IMAGE_TOKENS.get(model_id, _IMAGE_TOKENS_DEFAULT)
            )
            if modality == "image"
            else 0
        ),
        # Credits per second by (resolution, audio).
        "creditPerSecond": _video_rates(model_id) if modality == "video" else {},
        "aspects": imagegen.aspects_for(model_id) if modality == "image" else [],
        "creditCost": credit_cost,
        "inputCreditCost": input_credit_cost,
        "contextWindow": context,
        "supportsVision": bool(override.get("supports_vision", info.get("supports_vision"))),
        # `supported_openai_params` lists `tools` even for image endpoints.
        "supportsTools": bool(
            override.get(
                "supports_tools",
                modality == "chat"
                and (info.get("supports_function_calling") or "tools" in supported),
            )
        ),
        "adapter": None,
        "description": override.get("description", ""),
    }


def _adapter_entries() -> list[dict[str, Any]]:
    return [
        {
            "id": m["id"],
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
            "aspects": imagegen.aspects_for(m["id"]) if m["modality"] == "image" else [],
        }
        for m in ADAPTER_MODELS
    ]


def _video_rates(model_id: str) -> dict[str, int]:
    """`{"720p:silent": 3000, …}`: credits per second per clip shape; unpriced shapes absent."""
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


def fallback_order(model: dict[str, Any]) -> tuple[int, float]:
    """Sort key for the default model: non-strict-local first, then cheapest, then id.

    Strict-local is a route somebody picks on purpose, so it never wins a price
    tie. The id breaks ties deterministically; `KCHAT_DEFAULT_CHAT_MODEL` is
    where a preference belongs.
    """
    return (1 if model.get("strictLocal") else 0, model["creditCost"], model["id"])


def find(models: list[dict[str, Any]], model_id: str) -> dict[str, Any] | None:
    return next((m for m in models if m["id"] == model_id), None)


async def list_models(force: bool = False) -> dict[str, Any]:
    """Merged catalogue plus whether LiteLLM answered.

    `litellmAvailable: false` is a result, not an error: adapter models are still listed.
    """
    now = time.monotonic()
    if not force and _CACHE["value"] is not None and now - _CACHE["at"] < _CACHE_TTL_SEC:
        return _CACHE["value"]

    proxied: list[dict[str, Any]] = []
    available = True
    _unpriced.clear()
    try:
        # One row per deployment; first row per `model_name` wins.
        proxied_ids: set[str] = set()
        for entry in await litellm.model_info():
            shaped = _shape(entry)
            if shaped and shaped["id"] not in proxied_ids:
                proxied_ids.add(shaped["id"])
                proxied.append(shaped)
    except litellm.LiteLLMError as exc:
        log.warning("model catalogue falling back to adapters only: %s", exc)
        available = False

    seen = {m["id"] for m in proxied}
    merged = proxied + [m for m in _adapter_entries() if m["id"] not in seen]
    merged.sort(key=lambda m: (_MODALITY_ORDER.get(m["modality"], 9), m["provider"], m["id"]))

    def served(model_id: str, kind: str) -> str:
        """`model_id` if this install serves it for `kind`, else ``."""
        ok = any(m["id"] == model_id and kind in m["kinds"] for m in merged)
        return model_id if ok else ""

    default_chat = served(settings.default_chat_model, "chat")
    by_kind = {
        # The chat fallback is re-checked against each surface.
        kind: served(chosen, kind) or served(default_chat, kind)
        for kind, chosen in (
            ("report", settings.default_report_model),
            ("slides", settings.default_slides_model),
        )
    }
    # No chat fallback for images: the client takes the cheapest image model.
    if image_default := served(settings.default_image_model, "image"):
        by_kind["image"] = image_default
    # One default per modality on the audio/video surface, served only if the
    # model is of that modality. The surface's own default is the video one.
    by_mode = {
        mode: chosen
        for mode, chosen in (
            ("audio", served(settings.default_audio_model, "av")),
            ("video", served(settings.default_video_model, "av")),
        )
        if chosen and any(m["id"] == chosen and m["modality"] == mode for m in merged)
    }
    if "video" in by_mode:
        by_kind["av"] = by_mode["video"]
    result = {
        "models": merged,
        "litellmAvailable": available,
        "defaultChatModel": default_chat,
        "defaultModelByKind": by_kind,
        "defaultAvModelByMode": by_mode,
    }
    _CACHE.update(at=now, value=result)
    return result


#: Enrichment models already reported as absent, so the warning is logged once.
_MISSING_ENRICHMENT: set[str] = set()


async def resolve_enrichment_model() -> str:
    """`title_model` if the gateway serves it for chat, otherwise ""."""
    configured = settings.title_model
    if not configured:
        return ""
    catalogue = await list_models()
    if any(model["id"] == configured and "chat" in model["kinds"] for model in catalogue["models"]):
        return configured
    if configured not in _MISSING_ENRICHMENT:
        _MISSING_ENRICHMENT.add(configured)
        log.warning(
            "title_model %s is not in the catalogue; "
            "titles and memory extraction use the session model",
            configured,
        )
    return ""


async def list_models_for_egress() -> dict[str, Any]:
    """Uncached catalogue for privacy and routing decisions.

    An alias can be remapped within the cache window, so the gateway is asked
    again. If it cannot answer, the catalogue is empty and the turn fails closed.
    """
    catalogue = await list_models(force=True)
    if catalogue.get("litellmAvailable") is not True:
        return {
            **catalogue,
            "models": [],
            "defaultChatModel": "",
            "defaultModelByKind": {},
            "defaultAvModelByMode": {},
        }
    return catalogue


def invalidate_cache() -> None:
    _CACHE.update(at=0.0, value=None)
