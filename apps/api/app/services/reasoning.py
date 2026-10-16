"""Reasoning effort as `/llm` callers see it: the levels a model takes, and an ask for no
reasoning made answerable where the model cannot go without.

The levels come from LiteLLM `model_info` (`kchat_reasoning_*`, written from the
OpenRouter catalogue by kloudchat-llm's config generator).
"""

from __future__ import annotations

from typing import Any

#: Effort names from least to most; OpenRouter maps a name a model lacks to a near one.
ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def of(info: dict[str, Any]) -> dict[str, Any] | None:
    """`{efforts, default, mandatory}` for a model that takes an effort, else None."""
    efforts = info.get("kchat_reasoning_efforts")
    if not info.get("supports_reasoning") or not isinstance(efforts, list):
        return None
    named = [e for e in efforts if isinstance(e, str) and e in ORDER]
    if not named:
        return None
    default = info.get("kchat_reasoning_default")
    return {
        "efforts": sorted(named, key=ORDER.index),
        "default": default if isinstance(default, str) else None,
        "mandatory": bool(info.get("kchat_reasoning_mandatory")),
    }


def least(reasoning: dict[str, Any]) -> str:
    """The lowest effort above none the model takes."""
    above = [e for e in reasoning["efforts"] if e != "none"]
    return above[0] if above else "low"


def settle(body: dict[str, Any], reasoning: dict[str, Any] | None) -> bool:
    """Turns an ask for no reasoning into the least reasoning when the model's reasoning
    cannot be turned off (OpenRouter answers 400 otherwise). True when `body` changed.

    Covers chat completions (`reasoning_effort`, OpenRouter `reasoning`), the Responses API
    (`reasoning.effort`) and Anthropic messages (`thinking.type: disabled`)."""
    if not reasoning or not reasoning["mandatory"]:
        return False
    low = least(reasoning)
    changed = False
    if body.get("reasoning_effort") == "none":
        body["reasoning_effort"] = low
        changed = True
    asked = body.get("reasoning")
    if isinstance(asked, dict) and (asked.get("effort") == "none" or asked.get("enabled") is False):
        body["reasoning"] = {**{k: v for k, v in asked.items() if k != "enabled"}, "effort": low}
        changed = True
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        # The model thinks at its default; LiteLLM maps no "disabled" it could keep.
        body.pop("thinking")
        changed = True
    return changed
