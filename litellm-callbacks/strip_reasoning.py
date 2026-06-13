"""Strip reasoning_content from qwen3.5:122b responses.

The 122b (Deep Research brain) is a thinking model — vLLM's ``--reasoning-parser
qwen3`` correctly separates its chain-of-thought into ``reasoning_content`` and
LibreChat renders it as a collapsible "thinking" block. That reasoning is a long
English "Thinking Process: 1. Analyze the Request..." monologue whose length and
style can't be shortened via prompt (verified: a "reason concisely / in Korean"
system message leaves a 3600+ char English reasoning intact). To keep Deep
Research output clean we drop ``reasoning_content`` so only the final answer
(``content``) reaches the user — no thinking block.

Scoped to 122b (the only local thinking model). ``content`` is untouched, so the
answer is unaffected; only the separate reasoning channel is removed.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from litellm.integrations.custom_logger import CustomLogger

log = logging.getLogger("kc.strip_reasoning")


def _is_122b(model: Any) -> bool:
    return "122b" in (model or "")


def _drop(obj: Any) -> None:
    """Null reasoning_content on a message/delta if present."""
    if obj is not None and getattr(obj, "reasoning_content", None):
        try:
            obj.reasoning_content = None
        except Exception:
            pass


class ReasoningStripper(CustomLogger):
    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        try:
            if not _is_122b(getattr(response, "model", "")):
                return response
            for choice in getattr(response, "choices", None) or []:
                _drop(getattr(choice, "message", None))
        except Exception:
            log.exception("strip_reasoning (non-stream) error")
        return response

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data
    ) -> AsyncGenerator[Any, None]:
        async for chunk in response:
            try:
                if _is_122b(getattr(chunk, "model", "")):
                    for ch in getattr(chunk, "choices", None) or []:
                        _drop(getattr(ch, "delta", None))
            except Exception:
                pass
            yield chunk


stripper_instance = ReasoningStripper()


# Self-register on import (same pattern as sanitize_python_tag) — the proxy
# imports this module via litellm_settings.callbacks but doesn't reliably append
# the resolved instance to litellm.callbacks (the dispatch list).
try:
    import litellm as _litellm

    if not isinstance(_litellm.callbacks, list):
        _litellm.callbacks = []
    if stripper_instance not in _litellm.callbacks:
        _litellm.callbacks.append(stripper_instance)
except Exception:
    log.exception("strip_reasoning: self-registration failed")
