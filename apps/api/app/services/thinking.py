"""Thinking-starved answer detection; no-thinking settings (max_tokens covers reasoning+answer)."""

from __future__ import annotations

from app.core.config import settings

#: answer tokens added on top of spent thinking
_HEADROOM = 700

#: OpenRouter switch; JSON/markup writers only, never chat
NO_REASONING = {"enabled": False}

#: providers with mandatory reasoning, learned per process
_MANDATORY: set[str] = set()


#: vLLM drops `reasoning`; Qwen3 templates read enable_thinking
_SELF_HOSTED_PREFIXES = ("local/", "strict-local/")


def thinks_by_default(model: str) -> bool:
    """Whether self-hosted model thinks by default."""
    if not model.startswith(_SELF_HOSTED_PREFIXES):
        return False
    fragments = [f.strip() for f in settings.self_hosted_thinking_models.split(",") if f.strip()]
    return any(fragment in model for fragment in fragments)


def template_switch(model: str) -> dict:
    """vLLM template field turning Qwen3 thinking off; {} otherwise."""
    return {"chat_template_kwargs": {"enable_thinking": False}} if thinks_by_default(model) else {}


def template_switch_on(model: str) -> dict:
    """vLLM template field turning Qwen3 thinking on; {} otherwise."""
    return {"chat_template_kwargs": {"enable_thinking": True}} if thinks_by_default(model) else {}


#: fallback when reasoning can't be disabled: least effort
LEAST_REASONING = {"effort": "low"}


def switch(model: str) -> dict:
    """No-thinking fields for model (OpenRouter switch or least effort, plus vLLM switch)."""
    fields: dict = {"reasoning": LEAST_REASONING if model in _MANDATORY else NO_REASONING}
    fields.update(template_switch(model))
    return fields


def refused(model: str, response: object) -> bool:
    """Whether response is a refusal of the reasoning switch; remembered."""
    status = getattr(response, "status_code", None)
    if status != 400:
        return False
    try:
        body = str(getattr(response, "text", "") or "")
    except Exception:  # noqa: BLE001 — a body that cannot be read is not a refusal
        return False
    if "reasoning" in body.lower():
        _MANDATORY.add(model)
        return True
    return False


def starved(payload: dict, asked: int) -> int:
    """Larger max_tokens worth retrying with, or 0."""
    choices = payload.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    if choice.get("finish_reason") != "length":
        return 0
    message = choice.get("message") or {}
    if (message.get("content") or "").strip():
        # truncated but non-empty: parsers read partials
        return 0
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    thought = int(details.get("reasoning_tokens") or 0)
    if not thought:
        # no reasoning reported: answer itself overflowed
        thought = int(usage.get("completion_tokens") or 0)
    return asked + thought + _HEADROOM if thought else 0
