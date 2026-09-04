"""Detects an answer starved by a reasoning model's thinking; the no-thinking setting.

OpenAI-compatible `max_tokens` caps reasoning and answer together, so a
reasoning model can return `finish_reason: "length"` with empty content.
"""

from __future__ import annotations

#: Answer tokens added on top of the thinking the first attempt spent.
_HEADROOM = 700

#: OpenRouter `reasoning` field that turns thinking off. Sent by the writers
#: whose whole answer is one JSON object or one block of markup, never by chat.
NO_REASONING = {"enabled": False}


def starved(payload: dict, asked: int) -> int:
    """A bigger `max_tokens` worth re-asking with, or `0` when a re-ask would buy nothing."""
    choices = payload.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    if choice.get("finish_reason") != "length":
        return 0
    message = choice.get("message") or {}
    if (message.get("content") or "").strip():
        # Truncated but not empty: the callers' parsers read a partial answer.
        return 0
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    thought = int(details.get("reasoning_tokens") or 0)
    if not thought:
        # No reasoning reported: the answer itself did not fit.
        thought = int(usage.get("completion_tokens") or 0)
    return asked + thought + _HEADROOM if thought else 0
