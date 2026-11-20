"""Super Agent as a model on the /llm proxy: `kloudchat/super-agent` and its strict-local twin.

One chat turn per request: the conversation (`messages`) is routed by the same table, picks,
efforts and model health as a Super Agent turn in the app (`super_agent`), and the request goes
on to the gateway under the caller's own key, so budget, allowlist and credits apply as for any
model. Reports and slides are not offered here: they are long jobs, not one request and answer.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import or_
from sqlmodel import select

from app.core.db import SessionLocal
from app.models.user import AuditEvent, User, UserStatus
from app.services import adaptive_routing, governance, model_health, settings_store, super_agent
from app.services import models as model_service

log = logging.getLogger(__name__)

SUPER_ID = "kloudchat/super-agent"
STRICT_ID = "kloudchat/super-agent-strict-local"
#: the ids the proxy answers itself, and the entry each stands for
MODE_OF = {SUPER_ID: super_agent.SUPER, STRICT_ID: super_agent.SUPER_STRICT}

#: seconds a key's owner is remembered; a revoked key stops working within this
_CALLER_TTL = 60.0
_callers: dict[str, tuple[float, str]] = {}
_MAX_CALLERS = 4096


class ApiRouteError(Exception):
    """A refusal the caller gets as an OpenAI-shaped error."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


@dataclass(slots=True)
class Resolved:
    """What a Super Agent request becomes: where it goes and what is said about it."""

    model: dict[str, Any]
    route: dict[str, Any]
    #: reasoning fields to add to the request body ({} = the deployment's own setting)
    fields: dict[str, Any]
    #: the request carries personal data that must be masked on its way out
    mask: bool
    strict: bool


def bearer_of(header: str) -> str:
    """The key in an `Authorization: Bearer ...` header, or an empty string."""
    scheme, _, value = (header or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def _user_of(user_id: str) -> User | None:
    async with SessionLocal() as db:
        return (
            await db.exec(
                select(User).where(or_(User.id == user_id, User.litellm_user_id == user_id))
            )
        ).first()


async def caller_of(secret: str) -> User | None:
    """The account a gateway key belongs to, or None for an unknown or unusable key.

    The gateway is the authority on keys, so its `/key/info` answers (owner in the key's
    metadata, else its user id) instead of anything decrypted here; the answer is kept for
    `_CALLER_TTL` under the key's hash, so a busy client costs one lookup a minute."""
    if not secret:
        return None
    hashed = _digest(secret)
    now = time.monotonic()
    hit = _callers.get(hashed)
    if hit is not None and hit[0] > now:
        return await _user_of(hit[1])
    base, master = await settings_store.litellm_config()
    if not base or not master:
        return None
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {master}"},
            timeout=httpx.Timeout(5.0, connect=3.0),
        ) as client:
            response = await client.get("/key/info", params={"key": secret})
        if response.status_code != 200:
            return None
        info = (response.json() or {}).get("info") or {}
    except (httpx.HTTPError, ValueError):
        return None
    if info.get("blocked"):
        return None
    meta = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
    owner = str(meta.get("kchat_user_id") or info.get("user_id") or "")
    user = await _user_of(owner) if owner else None
    if user is None or user.status is not UserStatus.active:
        return None
    if len(_callers) >= _MAX_CALLERS:
        _callers.clear()
    _callers[hashed] = (now + _CALLER_TTL, user.id)
    return user


def forget_callers() -> None:
    """Drop every remembered key owner (tests)."""
    _callers.clear()


async def _catalogue_for(user: User) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`(usable chat models for this account, whole catalogue)`."""
    catalogue = await model_service.list_models_for_egress()
    allowed = set(user.allowed_models or [])
    usable = [
        m
        for m in catalogue.get("models") or []
        if "chat" in m.get("kinds", []) and (not allowed or m.get("id") in allowed)
    ]
    return usable, catalogue


async def listed(user: User) -> list[dict[str, Any]]:
    """The model ids this account may call, as `/v1/models` rows; none when the switch is off
    or no model of the entry is usable for the account."""
    if not await settings_store.api_super_agent_enabled():
        return []
    try:
        policy = await governance.current_for_egress()
        usable, _ = await _catalogue_for(user)
    except Exception:  # noqa: BLE001 — a listing never fails for its extra rows
        return []
    rules = super_agent.normalise_rules(policy.super_agent_rules)
    allowed = set(user.allowed_models or [])
    rows = []
    for model_id, mode in MODE_OF.items():
        strict = mode == super_agent.SUPER_STRICT
        if super_agent.estimate(rules, usable, strict=strict, allowed=allowed):
            rows.append({"id": model_id, "object": "model", "created": 0, "owned_by": "kloudchat"})
    return rows


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text") or "")
            for p in content
            if isinstance(p, dict) and p.get("type") in ("text", "input_text")
        )
    return ""


def _has_pictures(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(
            isinstance(p, dict) and p.get("type") in ("image_url", "input_image")
            for p in m["content"]
        )
        for m in messages
        if isinstance(m, dict)
    )


def _masked(messages: list[dict[str, Any]], *, legacy: bool) -> list[dict[str, Any]]:
    """`messages` with personal data masked in every text the model would read."""
    masker = governance.mask_legacy if legacy else governance.mask

    def clean(value: str) -> str:
        return masker(value)[0] if value else value

    out: list[dict[str, Any]] = []
    for message in messages:
        message = dict(message)
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = clean(content)
        elif isinstance(content, list):
            message["content"] = [
                {**part, "text": clean(part["text"])}
                if isinstance(part, dict) and isinstance(part.get("text"), str)
                else part
                for part in content
            ]
        out.append(message)
    return out


async def _blocked(user: User, hit: str) -> None:
    async with SessionLocal() as db:
        db.add(
            AuditEvent(
                actor_id=user.id,
                action="filter.blocked",
                target=user.email,
                detail=hit,
                severity="warn",
            )
        )
        await db.commit()


async def resolve(
    user: User,
    mode: str,
    payload: dict[str, Any],
    *,
    secret: str,
    skip: frozenset[str] = frozenset(),
) -> Resolved:
    """Route one chat request; raises `ApiRouteError` where nothing may answer it."""
    messages = [m for m in (payload.get("messages") or []) if isinstance(m, dict)]
    last_user = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None
    )
    if last_user is None:
        raise ApiRouteError(400, "invalid_request", "messages need a user message.")
    try:
        policy = await governance.current_for_egress()
    except governance.GovernanceUnavailable:
        raise ApiRouteError(
            503, "governance_unavailable", "The data policy could not be read."
        ) from None
    texts = [_text(m.get("content")) for m in messages]
    content = texts[last_user]
    if policy.intent_filter and (
        hit := governance.blocked_by("\n".join(texts), policy.blocked_categories)
    ):
        await _blocked(user, hit)
        raise ApiRouteError(422, f"blocked_category:{hit}", "This request is blocked by policy.")

    usable, catalogue = await _catalogue_for(user)
    default = model_service.pick_default(usable, catalogue, "chat")
    if default is None:
        raise ApiRouteError(503, "no_models_available", "No model is available.")
    rules = super_agent.normalise_rules(policy.super_agent_rules)
    allowed = set(user.allowed_models or [])
    tools = bool(payload.get("tools") or payload.get("functions"))
    pictures = _has_pictures(messages)
    history = tuple(
        (str(m.get("role") or ""), t, None)
        for m, t in zip(messages[:last_user], texts[:last_user], strict=True)
    )
    turn = super_agent.Turn(
        text=content,
        kind="chat",
        history=history,
        pictures=pictures,
        tool_bound=tools,
        context_bytes=adaptive_routing.estimated_context_tokens(texts),
    )
    decision = super_agent.classify(turn)
    personal = governance.findings(
        {"current_input": content, "conversation_history": texts[:last_user]},
        legacy=policy.pii_masking,
    )
    strict_variant = mode == super_agent.SUPER_STRICT
    strict = strict_variant or bool(personal)

    classified: super_agent.Classified | None = None
    classifier_id: str | None = None
    if decision.ambiguous and not personal:
        classifier_id = (
            str(policy.adaptive_classifier_model_id or "") or rules["simple"]["strictModel"]
        )
        classifier = model_service.find(usable, classifier_id)
        if adaptive_routing.classifier_is_usable(classifier, allowed_model_ids=allowed):
            classified = await super_agent.classify_with_model(
                turn, model_id=classifier_id, user_id=user.id, api_key=secret
            )
            if (
                classified is not None
                and classified.confidence >= super_agent.CLASSIFIER_MIN_CONFIDENCE
            ):
                # the classifier knows `code` only: code that draws data is `visual`
                decision = super_agent.visual_class(super_agent.Decision(
                    classified.task_class,
                    "classifier",
                    "classified",
                    confidence=classified.confidence,
                    signals=decision.signals,
                ))
    row, effort = super_agent.steer(rules, decision, super_agent.AUTO, strict=strict)

    def pick(strict_column: bool):
        return super_agent.pick(
            rules,
            row,
            usable,
            kind="chat",
            strict=strict_column,
            allowed=allowed,
            context_bytes=turn.context_bytes,
            needs_tools=tools,
            needs_vision=pictures,
            default=default,
            skip=skip | model_health.unhealthy(),
        )

    chosen, how = pick(strict)
    if chosen is None and strict and not strict_variant:
        # personal data and no strict-local model here: privacy masking carries the turn
        chosen, how = pick(False)
    if chosen is None and strict:
        raise ApiRouteError(
            503, "strict_local_unavailable", "No strict-local model can answer this request."
        )
    if chosen is None:
        raise ApiRouteError(503, "no_models_available", "No model can answer this request.")
    chosen_strict = super_agent.strict_model(chosen)
    if strict_variant and not chosen_strict:
        raise ApiRouteError(
            503, "strict_local_unavailable", "No strict-local model can answer this request."
        )
    mask = bool(personal) and (
        policy.pii_masking or (policy.external_data_guard and not chosen_strict)
    )
    fields = super_agent.reasoning_fields(str(chosen["id"]), effort, chosen)
    route = {
        "model": chosen["id"],
        "row": row,
        "effort": super_agent.applied_effort(fields),
        "strictLocal": chosen_strict,
        "fallback": how != "rule",
    }
    return Resolved(chosen, route, fields, mask, strict)


def apply(payload: dict[str, Any], resolved: Resolved, *, legacy: bool) -> dict[str, Any]:
    """The body the gateway gets: the chosen model, its reasoning fields (unless the caller
    set its own), masked text where needed, and no gateway fallback out of strict-local."""
    body = dict(payload)
    body["model"] = resolved.model["id"]
    own = ("reasoning", "reasoning_effort", "chat_template_kwargs", "thinking")
    for key, value in resolved.fields.items():
        if not any(k in body for k in own):
            body[key] = value
    if resolved.mask:
        body["messages"] = _masked(list(body.get("messages") or []), legacy=legacy)
    if resolved.strict or resolved.route["strictLocal"]:
        body["disable_fallbacks"] = True
    return body


__all__ = [
    "MODE_OF",
    "STRICT_ID",
    "SUPER_ID",
    "ApiRouteError",
    "Resolved",
    "apply",
    "bearer_of",
    "caller_of",
    "forget_callers",
    "listed",
    "resolve",
]
