"""Detects an answer starved by a reasoning model's thinking; the no-thinking setting.

OpenAI-compatible `max_tokens` caps reasoning and answer together, so a
reasoning model can return `finish_reason: "length"` with empty content.
"""

from __future__ import annotations

from app.core.config import settings

#: Answer tokens added on top of the thinking the first attempt spent.
_HEADROOM = 700

#: OpenRouter `reasoning` field that turns thinking off. Sent by the writers
#: whose whole answer is one JSON object or one block of markup, never by chat.
NO_REASONING = {"enabled": False}

#: Models whose provider answered that reasoning cannot be turned off
#: (「Reasoning is mandatory for this endpoint」). Learned per process.
_MANDATORY: set[str] = set()


#: Self-hosted routes run vLLM, where the OpenRouter `reasoning` field is dropped and a
#: Qwen3-family chat template reads `enable_thinking` instead. Templates that do not
#: know the variable ignore it.
_SELF_HOSTED_PREFIXES = ("local/", "strict-local/")


def thinks_by_default(model: str) -> bool:
    """Whether a self-hosted `model` is one of those configured as thinking unless told not to."""
    if not model.startswith(_SELF_HOSTED_PREFIXES):
        return False
    fragments = [f.strip() for f in settings.self_hosted_thinking_models.split(",") if f.strip()]
    return any(fragment in model for fragment in fragments)


def template_switch(model: str) -> dict:
    """vLLM's chat-template field that turns a thinking Qwen3 off; `{}` for other models."""
    return {"chat_template_kwargs": {"enable_thinking": False}} if thinks_by_default(model) else {}


#: What a model whose reasoning cannot be turned off is asked for instead: the least of
#: it, so thinking does not consume the whole token ceiling.
LEAST_REASONING = {"effort": "low"}


def switch(model: str) -> dict:
    """The fields that ask `model` for no thinking: the OpenRouter `reasoning` switch, or the
    least reasoning where its provider refused that, plus vLLM's chat-template switch."""
    fields: dict = {"reasoning": LEAST_REASONING if model in _MANDATORY else NO_REASONING}
    fields.update(template_switch(model))
    return fields


def refused(model: str, response: object) -> bool:
    """Whether `response` is the provider refusing the reasoning switch; remembers it.

    Reads the status and body defensively: test doubles and odd gateways need not carry
    both, and a response that is not a 400 about reasoning is simply not a refusal."""
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
