"""Model calls of 지식 서가 (guides, studio outputs, quizzes, audio scripts): which model may
read the project's sources, the call itself, and its bill.

Project sources are internal material. While the administrator keeps internal data on
strict-local models (`Governance.internal_data_strict_local`, on by default) only a
strict-local model reads them; otherwise the configured strict-local model is still the
default, and an allowed outside model may be asked for, its text masked for personal data
as the egress guard does elsewhere. Every call is a side call: billed at the full rate (no
cache discount), or written to the ledger as tokens on a self-hosted model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.models.user import User
from app.services import files as file_service
from app.services import governance
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
    for model in strict:
        if model.get("id") == wanted:
            return model
    # same family as the wanted one (27b / flash), then any
    family = wanted.rsplit("-", 1)[-1] if wanted else ""
    for model in strict:
        if family and str(model.get("id")).endswith(family):
            return model
    return strict[0] if strict else None


async def policy() -> Any:
    try:
        return await governance.current_for_egress()
    except governance.GovernanceUnavailable as exc:
        raise ShelfError("governance_unavailable", 503) from exc


async def route(user: User, *, role: str = "write", asked: str | None = None) -> Route:
    """The model this call may use. `role`: `write` (studio, quiz, script) or `guide`."""
    rules = await policy()
    catalogue = await model_service.list_models_for_egress()
    usable = _allowed(user, catalogue.get("models") or [])
    wanted = settings.knowledge_guide_model if role == "guide" else settings.knowledge_studio_model
    required = bool(rules.internal_data_strict_local)
    if asked:
        chosen = model_service.find(usable, asked)
        if chosen is None:
            raise ShelfError("model_not_allowed", 422)
        if required and not is_strict(chosen):
            raise ShelfError("internal_data_requires_strict_local")
        strict = is_strict(chosen)
        return Route(chosen, strict, required,
                     not strict and (rules.external_data_guard or rules.pii_masking))
    picked = _strict_pick(usable, wanted)
    if picked is not None:
        return Route(picked, True, required, False)
    if required:
        raise ShelfError("no_strict_local_model", 503)
    default = model_service.find(usable, str(catalogue.get("defaultChatModel") or ""))
    fallback = default or (usable[0] if usable else None)
    if fallback is None:
        raise ShelfError("no_models", 503)
    strict = is_strict(fallback)
    return Route(fallback, strict, False,
                 not strict and (rules.external_data_guard or rules.pii_masking))


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


async def complete(user: User, model: dict, system: str, prompt: str,
                   max_tokens: int) -> tuple[str, dict]:
    """One non-streaming call; `(text, usage)`."""
    # late import: `report` pulls in the pipeline
    from app.services import report

    await litellm_service.ensure_key(user)
    _, api_key = await litellm_service.credentials_for(user)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    return await report._complete(str(model["id"]), messages, api_key, max_tokens)


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
