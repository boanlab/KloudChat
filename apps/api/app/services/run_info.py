"""What an answer ran on: provider, quantization, fallback and the call's settings.

Public details under an answer (「이번 답」): the model's declared facts (`ModelInfo.runInfo`), the
provider the gateway reported, and the sampling settings sent. The system prompt appears only as a
short hash.
"""

from __future__ import annotations

import hashlib
from typing import Any

#: settings shown as sent; a key the app did not send is not shown (the model's own default)
_SETTINGS = (("temperature", "temperature"), ("top_p", "topP"), ("seed", "seed"),
             ("max_tokens", "maxTokens"), ("presence_penalty", "presencePenalty"))


def _effort(payload: dict[str, Any]) -> str | None:
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        if isinstance(reasoning.get("effort"), str):
            return reasoning["effort"]
        if reasoning.get("enabled") is False or reasoning.get("exclude") is True:
            return "off"
    if isinstance(payload.get("reasoning_effort"), str):
        return payload["reasoning_effort"]
    kwargs = payload.get("chat_template_kwargs")
    if isinstance(kwargs, dict) and isinstance(kwargs.get("enable_thinking"), bool):
        return "on" if kwargs["enable_thinking"] else "off"
    return None


def prompt_hash(messages: list[dict[str, Any]]) -> str | None:
    """Short hash of the system message(s), for telling two prompts apart without showing one."""
    parts = [m.get("content") for m in messages if m.get("role") == "system"]
    text = "\n".join(p if isinstance(p, str) else repr(p) for p in parts if p)
    return hashlib.sha256(text.encode()).hexdigest()[:12] if text else None


def call_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """The sampling settings and system-prompt hash of one chat call's payload."""
    out: dict[str, Any] = {}
    for key, name in _SETTINGS:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[name] = value
    effort = _effort(payload)
    if effort:
        out["reasoning"] = effort
    digest = prompt_hash(payload.get("messages") or [])
    if digest:
        out["systemPromptHash"] = digest
    return out


def record(requested: dict[str, Any], event: dict[str, Any], catalogue: list[dict[str, Any]],
           served_by: str = "") -> dict[str, Any]:
    """`routing.runInfo` for a message.

    `requested` is the model asked for; `served_by` names the fallback that answered, if any.
    Declared facts come from the answering model's catalogue row; a pinned provider's
    quantization applies only when that provider answered.
    """
    served = served_by or str(requested.get("id") or "")
    row = next((m for m in catalogue if m.get("id") == served), None) or {}
    declared = row.get("runInfo") or {}
    provider = event.get("provider") if isinstance(event.get("provider"), str) else None
    pinned = declared.get("providerPinned")
    quantization = declared.get("quantization")
    pins = {p.strip().casefold() for p in (pinned or "").split("|")}
    if pinned and provider and provider.casefold() not in pins:
        # answered by a provider other than the pin: the declared quantization is not its
        quantization = None
    out: dict[str, Any] = {
        "model": served,
        "provider": provider,
        "fallback": bool(served_by),
        "quantization": quantization,
        "kvCache": declared.get("kvCache"),
        "engine": declared.get("engine"),
        "providerPinned": pinned,
        "zdr": declared.get("zdr"),
        "policy": declared.get("policy"),
        "call": event.get("call") or {},
    }
    return {k: v for k, v in out.items() if v not in (None, "", {})}


__all__ = ["call_settings", "prompt_hash", "record"]
