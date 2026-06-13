"""Re-inject the original caller's user_id into requests routed to
super-agent so the shim can attribute downstream calls correctly.

Why this exists
---------------
LiteLLM's OpenAI request transformation only treats `user` as a supported
param for models registered in `litellm.open_ai_chat_completion_models`.
Combined with `drop_unsupported_params: true`, this means super-agent-shim
— which uses the custom name `openai/super-agent` for its api_base route —
receives a body without `user`. The shim then has no way to identify the
original caller, so its downstream router/chat calls back into LiteLLM fall
back to master-key auth and spend lands on `default_user_id` rather than
the user who actually invoked the agent.

Fix
---
Inject `data["extra_body"]["user"] = user_api_key_dict.user_id` in the
async_pre_call_hook. For openai-compatible providers LiteLLM flattens
`extra_body` into the outgoing request body, so the shim receives
`body["user"]` exactly as if the client had set it.

Only fires for super-agent model_names — other calls are untouched.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Union

from litellm.integrations.custom_logger import CustomLogger

log = logging.getLogger("litellm-super-agent-user-injector")

# Model names whose backend is super-agent-shim. Match the user-facing
# `model_name` (what LibreChat / users request), not the routed
# litellm_params.model — the hook runs before model_name resolution.
TARGET_MODELS: frozenset[str] = frozenset({"local/auto-route"})


class SuperAgentUserInjector(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,  # litellm.proxy._types.UserAPIKeyAuth
        cache: Any,              # litellm.caching.DualCache
        data: dict,
        call_type: str,
    ) -> Optional[Union[Exception, str, dict]]:
        try:
            model = data.get("model") if isinstance(data, dict) else None
            if model not in TARGET_MODELS:
                return None
            user_id = getattr(user_api_key_dict, "user_id", None)
            if not user_id:
                return None
            extra_body = data.get("extra_body")
            if not isinstance(extra_body, dict):
                extra_body = {}
            # `data["user"]` is irrelevant here — LiteLLM strips it before forwarding
            # for non-registered openai model names (super-agent's case). Only an
            # explicit `extra_body["user"]` should block injection, and that's rare.
            if "user" not in extra_body:
                extra_body["user"] = user_id
                data["extra_body"] = extra_body
            return data
        except Exception:
            log.exception("super-agent user injector: pre_call hook failed")
            return None


injector_instance = SuperAgentUserInjector()


# Self-register on litellm.callbacks so the hook dispatcher finds us even
# when CONFIG only mentions the module path (matches sanitize_python_tag's pattern).
try:
    import litellm as _litellm
    if injector_instance not in _litellm.callbacks:
        _litellm.callbacks.append(injector_instance)
except Exception:
    log.exception("super-agent user injector: self-register failed")
