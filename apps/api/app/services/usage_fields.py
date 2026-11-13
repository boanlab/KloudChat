"""Token counts read from a provider's `usage` object, cached prompt tokens included.

Providers name the cached part of the prompt differently: OpenAI-style (LiteLLM, vLLM,
OpenRouter) `prompt_tokens_details.cached_tokens`, DeepSeek `prompt_cache_hit_tokens`,
Anthropic through LiteLLM `cache_read_input_tokens`. Cached tokens are part of
`prompt_tokens`, not on top of it. `cachedInputTokens` is kept only when non-zero, so a
provider that reports none leaves usage dicts as they were."""

from __future__ import annotations

from typing import Any

CACHED = "cachedInputTokens"


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def cached_tokens(raw: dict[str, Any] | None) -> int:
    """Cached prompt tokens in one `usage` object, 0 when the provider reports none."""
    if not isinstance(raw, dict):
        return 0
    details = raw.get("prompt_tokens_details")
    nested = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
    return max(nested, _int(raw.get("prompt_cache_hit_tokens")),
               _int(raw.get("cache_read_input_tokens")))


def spent(raw: dict[str, Any] | None) -> dict[str, int]:
    """`usage` object → `{"inputTokens", "outputTokens"[, "cachedInputTokens"]}`."""
    raw = raw if isinstance(raw, dict) else {}
    out = {"inputTokens": _int(raw.get("prompt_tokens")),
           "outputTokens": _int(raw.get("completion_tokens"))}
    if cached := cached_tokens(raw):
        out[CACHED] = cached
    return out


def add_cached(total: dict[str, Any], part: dict[str, Any] | None, key: str = CACHED) -> None:
    """Adds `part`'s cached prompt tokens into `total[key]` (nothing when there are none)."""
    if isinstance(part, dict) and (cached := _int(part.get(CACHED))):
        total[key] = _int(total.get(key)) + cached


def merged_raw(first: dict[str, Any] | None, again: dict[str, Any] | None) -> dict[str, Any]:
    """Two calls' `usage` objects as one (a re-asked call is charged and counted with the
    first), cached tokens kept."""
    first = first if isinstance(first, dict) else {}
    again = again if isinstance(again, dict) else {}
    out: dict[str, Any] = {
        "prompt_tokens": _int(first.get("prompt_tokens")) + _int(again.get("prompt_tokens")),
        "completion_tokens": (_int(first.get("completion_tokens"))
                              + _int(again.get("completion_tokens"))),
    }
    if cached := cached_tokens(first) + cached_tokens(again):
        out["prompt_tokens_details"] = {"cached_tokens": cached}
    return out
