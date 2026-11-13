"""Model calls of 지식 서가 (guides, studio outputs, quizzes, audio scripts): which model may
read the project's sources, the call itself, and its bill.

Project sources are internal material. While the administrator keeps internal data on
strict-local models (`Governance.internal_data_strict_local`, on by default) only a
strict-local model reads them; otherwise each output kind's outside pick (`shelf_models`) is
used where served and allowed, its text masked for personal data as the egress guard does
elsewhere. Every call is a side call: billed at the full rate (no
cache discount), or written to the ledger as tokens on a self-hosted model.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.models.user import User
from app.services import files as file_service
from app.services import governance, settings_store, shelf_models, thinking
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services.credits import charge_for_tokens, record_units, settle

log = logging.getLogger(__name__)


class ShelfError(Exception):
    """A refusal the router turns into an HTTP error (`code` is the wire detail)."""

    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(slots=True)
class Route:
    model: dict[str, Any]
    #: the model is strict-local (sources stay inside)
    strict: bool
    #: the administrator requires strict-local for internal material
    required: bool
    #: text going out is masked for personal data
    mask: bool
    #: reasoning effort for the call (`none`, `high`)
    effort: str = "none"


def is_strict(model: dict | None) -> bool:
    return bool(model) and model.get("dataBoundary") == "self_hosted" \
        and model.get("strictLocal") is True


def _allowed(user: User, models: list[dict]) -> list[dict]:
    allowed = set(user.allowed_models or [])
    return [m for m in models if "chat" in (m.get("kinds") or [])
            and (not allowed or m.get("id") in allowed)]


def _strict_pick(usable: list[dict], wanted: str) -> dict | None:
    strict = [m for m in usable
              if is_strict(m) and str(m.get("id") or "").startswith("strict-local/")]
    for model_id in (wanted, *shelf_models.STRICT_FALLBACKS):
        found = next((m for m in strict if m.get("id") == model_id), None)
        if found is not None:
            return found
    return strict[0] if strict else None


async def policy() -> Any:
    try:
        return await governance.current_for_egress()
    except governance.GovernanceUnavailable as exc:
        raise ShelfError("governance_unavailable", 503) from exc


async def route(user: User, *, kind: str = "study_guide", asked: str | None = None,
                role: str | None = None) -> Route:
    """The model this call may use (`shelf_models`): the kind's strict-local pick while
    internal data must stay inside, else its `model` pick when served and allowed; a model
    the person asked for is checked against the same rule."""
    kind = role or kind
    rules = await policy()
    catalogue = await model_service.list_models_for_egress()
    usable = _allowed(user, catalogue.get("models") or [])
    row = shelf_models.pick(kind)
    effort = row.get("effort") or "none"
    required = bool(rules.internal_data_strict_local)
    guarded = bool(rules.external_data_guard or rules.pii_masking)
    if asked:
        chosen = model_service.find(usable, asked)
        if chosen is None:
            raise ShelfError("model_not_allowed", 422)
        if required and not is_strict(chosen):
            raise ShelfError("internal_data_requires_strict_local")
        strict = is_strict(chosen)
        return Route(chosen, strict, required, not strict and guarded, effort)
    if not required:
        outside = model_service.find(usable, row.get("model") or "")
        if outside is not None:
            strict = is_strict(outside)
            return Route(outside, strict, False, not strict and guarded, effort)
    picked = _strict_pick(usable, row.get("strictModel") or "")
    if picked is not None:
        return Route(picked, True, required, False, effort)
    if required:
        raise ShelfError("no_strict_local_model", 503)
    default = model_service.find(usable, str(catalogue.get("defaultChatModel") or ""))
    fallback = default or (usable[0] if usable else None)
    if fallback is None:
        raise ShelfError("no_models", 503)
    strict = is_strict(fallback)
    return Route(fallback, strict, False, not strict and guarded, effort)


def outgoing(text: str, how: Route) -> str:
    """Text as it may leave: masked for personal data on an outside model when the guard is on."""
    if not how.mask:
        return text
    return governance.mask(text)[0]


def tokens_of(text: str) -> int:
    return file_service.estimate_tokens(text)


def estimate(model: dict, input_tokens: int, output_tokens: int) -> int:
    """Credits a call would cost at the full rate (side calls get no cache discount)."""
    return charge_for_tokens(model, input_tokens, output_tokens)


def _fields(model_id: str, effort: str) -> dict[str, Any]:
    """Reasoning fields for `effort`: thinking on (the model's template switch, or the
    gateway's effort) or off."""
    if effort in ("low", "high"):
        if model_id.startswith(("local/", "strict-local/")):
            return thinking.template_switch_on(model_id) or {
                "chat_template_kwargs": {"enable_thinking": True}}
        return {"reasoning": {"effort": effort}}
    return thinking.switch(model_id)


_GATE: dict[int, asyncio.Semaphore] = {}


def _gate() -> asyncio.Semaphore:
    """Calls in flight per replica (`KNOWLEDGE_CONCURRENCY`): the self-hosted models serve
    conversations too."""
    loop = id(asyncio.get_running_loop())
    if loop not in _GATE:
        _GATE.clear()
        _GATE[loop] = asyncio.Semaphore(max(1, settings.knowledge_concurrency))
    return _GATE[loop]


async def _post(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One chat completion; a timeout, 429 or 5xx tried once more."""
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"), headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        for attempt in range(2):
            try:
                response = await client.post("/v1/chat/completions", json=payload)
            except httpx.TimeoutException:
                if attempt:
                    raise
                continue
            if response.status_code in (429, 502, 503, 504) and not attempt:
                await asyncio.sleep(3)
                continue
            response.raise_for_status()
            return response.json()
    raise httpx.HTTPError("no answer")


_THOUGHT = re.compile(r"<think>.*?</think>", re.S)
#: thinking room on top of the answer: flash thinks 4-8k tokens over a ten-file project
THINK_ROOM = 14_000


async def complete(user: User, model: dict, system: str, prompt: str,
                   max_tokens: int, effort: str = "none") -> tuple[str, dict]:
    """One non-streaming call; `(text, usage)`. With `effort` on, the answer budget gets the
    thinking room on top."""
    await litellm_service.ensure_key(user)
    _, api_key = await litellm_service.credentials_for(user)
    model_id = str(model["id"])
    thinks = effort in ("low", "high")
    payload = {
        "model": model_id,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens + THINK_ROOM if thinks else thinking.room(model_id, max_tokens),
        **_fields(model_id, effort),
    }
    async with _gate():
        data = await _post(api_key, payload)
    choice = (data.get("choices") or [{}])[0]
    text = _THOUGHT.sub("", str((choice.get("message") or {}).get("content") or ""))
    usage = data.get("usage") or {}
    spent = {"inputTokens": int(usage.get("prompt_tokens") or 0),
             "outputTokens": int(usage.get("completion_tokens") or 0)}
    if thinks and choice.get("finish_reason") == "length":
        # thinking ran past its room (flash sometimes loops): answered once without it
        log.info("%s thought past its room; asked again without thinking", model_id)
        again, more = await complete(user, model, system, prompt, max_tokens, effort="none")
        return again, {key: spent[key] + more[key] for key in spent}
    return text, spent


def bill(db, user: User, model: dict, usage: dict | None, reason: str) -> tuple[int, int, int]:
    """Charge one side call; `(credits, input tokens, output tokens)`. The caller commits."""
    spent_in = int((usage or {}).get("inputTokens") or (usage or {}).get("prompt_tokens") or 0)
    spent_out = int((usage or {}).get("outputTokens")
                    or (usage or {}).get("completion_tokens") or 0)
    credits = charge_for_tokens(model, spent_in, spent_out)
    if credits:
        settle(db, user, credits, reason=reason, model=str(model["id"]))
    else:
        record_units(db, user, reason=reason, model=str(model["id"]),
                     units=spent_in + spent_out, unit="tokens")
    return credits, spent_in, spent_out


def headroom(user: User, credits: int) -> bool:
    return credits <= 0 or user.credits_remaining >= credits


__all__ = ["Route", "ShelfError", "bill", "complete", "estimate", "headroom", "is_strict",
           "outgoing", "policy", "route", "tokens_of"]
