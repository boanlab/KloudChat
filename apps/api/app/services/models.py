"""Merged model catalogue; zero-priced remote models hidden (fail closed)."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, date, datetime
from typing import Any

from app.core.config import settings
from app.services import imagegen, litellm, reasoning, settings_store
from app.services.adapters import (
    ADAPTER_MODELS,
    FREE_PROVIDERS,
    MODEL_OVERRIDES,
    is_internal_api_base,
)

log = logging.getLogger(__name__)

#: output tokens per image (measured on gemini-2.5-flash-image, gpt-5-image-mini; rest by family)
_IMAGE_TOKENS_DEFAULT = 5450
_IMAGE_TOKENS = {
    "google/gemini-2.5-flash-image": 1290,
    "google/gemini-3-pro-image": 1290,
    "openai/gpt-5-image-mini": 5450,
    "openai/gpt-5-image": 5450,
    # OpenRouter prices these per 1K image as 4,175 tokens
    "bytedance-seed/seedream-5-0-flash": 4175,
    "x-ai/grok-imagine-image-2.0": 4175,
    "recraft/recraft-v4.1": 4175,
    # one 16:9 picture: 3,866 output tokens, usage.cost $0.127
    "openai/gpt-5.4-image-2": 3866,
}

#: `{model_id: provider}` hidden for missing price, rebuilt per refresh
_unpriced: dict[str, str] = {}


def unpriced() -> dict[str, str]:
    """Models hidden for missing price, latest build."""
    return dict(_unpriced)


#: LiteLLM `mode` → modality; unlisted = infrastructure
_MODE_MAP: dict[str, str] = {
    "chat": "chat",
    "completion": "chat",
    "image_generation": "image",
    "audio_speech": "audio",
}

#: modality → selectable surfaces
_KINDS_FOR: dict[str, list[str]] = {
    "chat": ["chat", "report", "slides"],
    "image": ["image"],
    "audio": ["av"],
    "video": ["av"],
}

#: upper-case label tokens
_ACRONYMS = {"gpt", "ai", "llm", "xtts", "ltx", "tts", "stt", "hd", "sd", "vl", "glm"}

#: vendor names by id prefix; else title-cased slug
_VENDORS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "x-ai": "xAI",
    "bytedance-seed": "ByteDance",
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

#: `local/<name>` vendor by name substring
_LOCAL_VENDORS = (
    ("qwen", "Qwen"),
    ("glm", "Z.ai"),
    ("gemma", "Google"),
    ("llama", "Meta"),
    ("mistral", "Mistral"),
    ("deepseek", "DeepSeek"),
    ("flux", "Black Forest Labs"),
    ("z-image", "Tongyi"),
)


#: route prefixes (where it runs), shown on the label
_ROUTES = ("local", "strict-local")


def _vendor(model_id: str, provider: str) -> str:
    """Model vendor for display."""
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
#: retry delay after a failed refresh (last catalogue served)
_RETRY_AFTER_FAILURE_SEC = 5.0

_DATA_BOUNDARIES = {"self_hosted", "hybrid", "external"}
_CUTOFF_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?")


def _knowledge_cutoff(info: dict[str, Any]) -> str | None:
    """Validated proxy-declared cutoff month (not an attestation)."""
    declared = [info[key] for key in ("knowledge_cutoff", "training_cutoff") if key in info]
    if not declared:
        return None
    cutoffs: set[str] = set()
    today = datetime.now(UTC).date()
    for raw in declared:
        if not isinstance(raw, str) or _CUTOFF_FORMAT.fullmatch(raw) is None:
            return None
        try:
            cutoff = date.fromisoformat(raw if len(raw) == 10 else f"{raw}-01")
        except ValueError:
            return None
        if cutoff > today:
            return None
        cutoffs.add(raw[:7])
    return next(iter(cutoffs)) if len(cutoffs) == 1 else None


def _data_boundary(info: dict[str, Any]) -> tuple[str, bool, bool]:
    """`(boundary, strict_local, privacy_only)` from declared metadata only."""
    raw = info.get("kchat_data_boundary")
    boundary = raw if isinstance(raw, str) and raw in _DATA_BOUNDARIES else "unknown"
    strict = boundary == "self_hosted" and info.get("kchat_strict_local") is True
    privacy_only = strict and info.get("kchat_privacy_only") is True
    return boundary, strict, privacy_only


def contained(info: dict[str, Any]) -> bool:
    """Whether declared metadata makes it strict-local."""
    return _data_boundary(info)[1]


def _credits(usd: float) -> int:
    """USD → credits, rounded up (priced never free)."""
    if usd <= 0:
        return 0
    return max(1, round(usd * settings.credits_per_usd))


def _label(model_id: str) -> str:
    """`anthropic/claude-opus-4.8` → `Claude Opus 4.8`; route prefixes in brackets."""
    head, _, tail = model_id.rpartition("/")
    # OpenRouter `:free` = zero cost
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
            # version-ish tokens keep shape (4.8, v2, 122b)
            out.append(word[0].upper() + word[1:] if word[0].isalpha() else word)
        else:
            out.append(word.capitalize())
    return (" ".join(out) or model_id) + route


def _shape(entry: dict[str, Any]) -> dict[str, Any] | None:
    """`/model/info` row → `ModelInfo`, or None if unlisted."""
    model_id = entry.get("model_name")
    if not model_id:
        return None

    info = entry.get("model_info") or {}
    override = MODEL_OVERRIDES.get(model_id, {})

    # routing-only deployments (failover twins) opt out
    if info.get("kchat_hidden"):
        return None

    # override, then LiteLLM `mode`, then chat
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

    # input billed separately
    input_credit_cost = 0
    if "input_credit_cost" in override:
        input_credit_cost = int(override["input_credit_cost"])
    elif modality == "chat":
        input_credit_cost = _credits(float(info.get("input_cost_per_token") or 0) * 1000)

    if "credit_cost" in override:
        credit_cost = int(override["credit_cost"])
    elif modality == "chat":
        # per 1k output tokens
        credit_cost = _credits(float(info.get("output_cost_per_token") or 0) * 1000)
    elif modality == "image":
        # picture models bill output tokens; `output_cost_per_image` is zero
        credit_cost = _credits(float(info.get("output_cost_per_image") or 0)) or _credits(
            float(info.get("output_cost_per_token") or 0) * 1000
        )
    else:
        # audio: per char (TTS, 900 ≈ 1 min), per output token, per request
        credit_cost = (
            _credits(float(info.get("output_cost_per_character") or 0) * 900)
            or _credits(float(info.get("output_cost_per_token") or 0) * 1000)
            or _credits(float(info.get("output_cost_per_request") or 0))
        )

    # zero trusted only for self-hosting, internal api_base, `:free`, or override
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

    # `label` = "Vendor · Model"; `name`/`vendor` kept separate
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
        "knowledgeCutoff": _knowledge_cutoff(info),
        "modality": modality,
        "kinds": kinds,
        # per-call/per-image prices for near-zero-output models
        "creditPerCall": _credits(float(info.get("output_cost_per_request") or 0)),
        "creditPerImage": (
            _credits(
                float(info.get("output_cost_per_token") or 0)
                * _IMAGE_TOKENS.get(model_id, _IMAGE_TOKENS_DEFAULT)
            )
            if modality == "image"
            else 0
        ),
        # credits per second by (resolution, audio)
        "creditPerSecond": _video_rates(model_id) if modality == "video" else {},
        "aspects": imagegen.aspects_for(model_id) if modality == "image" else [],
        "creditCost": credit_cost,
        "inputCreditCost": input_credit_cost,
        "contextWindow": context,
        "supportsVision": bool(override.get("supports_vision", info.get("supports_vision"))),
        # `supported_openai_params` lists `tools` even for image endpoints
        "supportsTools": bool(
            override.get(
                "supports_tools",
                modality == "chat"
                and (info.get("supports_function_calling") or "tools" in supported),
            )
        ),
        # Effort levels the model takes; None where it takes none (`/llm/v1/models` shows it).
        "reasoning": reasoning.of(info) if modality == "chat" else None,
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
            "knowledgeCutoff": None,
            "modality": m["modality"],
            "kinds": m["kinds"],
            "creditCost": m["credit_cost"],
            # adapters billed per asset
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
    """`{"720p:silent": 3000, …}` credits per second; unpriced shapes absent."""
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
    """Default-model sort key: non-strict-local, cheapest, id."""
    return (1 if model.get("strictLocal") else 0, model["creditCost"], model["id"])


def find(models: list[dict[str, Any]], model_id: str) -> dict[str, Any] | None:
    return next((m for m in models if m["id"] == model_id), None)


#: Picture model of a strict-local turn when the person has not picked one.
STRICT_IMAGE_DEFAULT = "strict-local/z-image-turbo"


def strict_image_model(models: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    """Strict-local picture model: the person's pick if served, else the default, else the first."""
    chosen = find(models, wanted) or find(models, STRICT_IMAGE_DEFAULT)
    return chosen or (models[0] if models else None)


async def list_models(force: bool = False) -> dict[str, Any]:
    """Merged catalogue plus LiteLLM availability (unavailable is a result, not an error)."""
    now = time.monotonic()
    if not force and _CACHE["value"] is not None and now - _CACHE["at"] < _CACHE_TTL_SEC:
        return _CACHE["value"]

    proxied: list[dict[str, Any]] = []
    available = True
    _unpriced.clear()
    try:
        # first row wins, except cutoff: all deployments (incl. hidden twins) must agree
        proxied_ids: set[str] = set()
        declared_cutoffs: dict[str, set[str | None]] = {}
        for entry in await litellm.model_info():
            model_id = entry.get("model_name")
            if isinstance(model_id, str):
                declared_cutoffs.setdefault(model_id, set()).add(
                    _knowledge_cutoff(entry.get("model_info") or {})
                )
            shaped = _shape(entry)
            if shaped and shaped["id"] not in proxied_ids:
                proxied_ids.add(shaped["id"])
                proxied.append(shaped)
        for model in proxied:
            cutoffs = declared_cutoffs.get(model["id"], set())
            model["knowledgeCutoff"] = next(iter(cutoffs)) if len(cutoffs) == 1 else None
    except litellm.LiteLLMError as exc:
        cached = _CACHE["value"]
        if not force and cached is not None and cached.get("litellmAvailable"):
            # keep last catalogue on transient gateway failure (forced reads fail closed)
            log.warning("model catalogue refresh failed, keeping the last one: %s", exc)
            _CACHE["at"] = now - _CACHE_TTL_SEC + _RETRY_AFTER_FAILURE_SEC
            return cached
        log.warning("model catalogue falling back to adapters only: %s", exc)
        available = False

    seen = {m["id"] for m in proxied}
    merged = proxied + [m for m in _adapter_entries() if m["id"] not in seen]
    merged.sort(key=lambda m: (_MODALITY_ORDER.get(m["modality"], 9), m["provider"], m["id"]))

    def served(model_id: str, kind: str) -> str:
        """`model_id` if served for `kind`, else ``."""
        ok = any(m["id"] == model_id and kind in m["kinds"] for m in merged)
        return model_id if ok else ""

    roles = await settings_store.model_roles()
    default_chat = served(roles.chat, "chat")
    by_kind = {
        # chat fallback rechecked per surface
        kind: served(chosen, kind) or served(default_chat, kind)
        for kind, chosen in (
            ("report", roles.report),
            ("slides", roles.slides),
        )
    }
    # no chat fallback for images
    if image_default := served(roles.image, "image"):
        by_kind["image"] = image_default
    # one default per audio/video modality; surface default = video
    by_mode = {
        mode: chosen
        for mode, chosen in (
            ("audio", served(roles.audio, "av")),
            ("video", served(roles.video, "av")),
        )
        if chosen and any(m["id"] == chosen and m["modality"] == mode for m in merged)
    }
    if "video" in by_mode:
        by_kind["av"] = by_mode["video"]
    strict_images = sorted(
        (
            m for m in merged
            if "image" in m["kinds"]
            and m.get("strictLocal")
            and m.get("dataBoundary") == "self_hosted"
        ),
        key=fallback_order,
    )
    strict_image = strict_image_model(strict_images, "")
    result = {
        "models": merged,
        "litellmAvailable": available,
        "defaultChatModel": default_chat,
        "defaultModelByKind": by_kind,
        "defaultAvModelByMode": by_mode,
        "strictImageDefault": strict_image["id"] if strict_image else "",
    }
    _CACHE.update(at=now, value=result)
    return result


#: enrichment models already warned about
_MISSING_ENRICHMENT: set[str] = set()


async def resolve_enrichment_model() -> str:
    """`title_model` if served for chat, else ""."""
    configured = (await settings_store.model_roles()).title
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
    """Uncached catalogue for privacy/routing; empty on gateway failure (fail closed)."""
    catalogue = await list_models(force=True)
    if catalogue.get("litellmAvailable") is not True:
        return {
            **catalogue,
            "models": [],
            "defaultChatModel": "",
            "defaultModelByKind": {},
            "defaultAvModelByMode": {},
            "strictImageDefault": "",
        }
    return catalogue


def invalidate_cache() -> None:
    _CACHE.update(at=0.0, value=None)
