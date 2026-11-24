"""How a new chat, report, slides or sheet conversation starts when the caller does not say.

Super Agent is the default on the four routed surfaces. The admin's per-surface default
(`settings_store.ROUTING_CHAT`) may instead be `super_strict` or a fixed model (`manual`); a model
fixed by the caller or the conversation's agent wins, and the person's own pick beats the admin
default. A project conversation keeping internal material strict-local starts on `super_strict`.
A chosen entry with no model this person may use starts on the surface's model; `super_strict`
never widens to `super`.
"""

from __future__ import annotations

from app.models.chat import RoutingMode, SessionKind
from app.models.user import User
from app.services import governance, settings_store, super_agent
from app.services import models as model_service

ROUTED_KINDS = frozenset(
    {SessionKind.chat, SessionKind.report, SessionKind.slides, SessionKind.sheet}
)


def personal_pick(user: User, kind: SessionKind) -> str:
    """The model this person picked for `kind` on the home screen, or ""."""
    prefs = user.preferences if isinstance(user.preferences, dict) else {}
    picks = prefs.get("modelByKind") or prefs.get("model_by_kind") or {}
    value = picks.get(kind.value) if isinstance(picks, dict) else None
    return value if isinstance(value, str) else ""


async def surface_start(kind: SessionKind) -> RoutingMode:
    """The admin's start mode for `kind` (`manual` off the routed surfaces)."""
    if kind not in ROUTED_KINDS:
        return RoutingMode.manual
    roles = await settings_store.model_roles()
    return RoutingMode(roles.start(kind.value))


async def usable_super(
    mode: RoutingMode, user: User, *, project_id: str | None = None
) -> RoutingMode:
    """`mode` (a Super Agent entry) as this conversation can run it: strict-local projects move
    `super` to `super_strict`; an entry with no usable model gives way to the other entry (`super`
    only to `super_strict`), else to `manual`."""
    policy = await governance.current()
    catalogue = (await model_service.list_models()).get("models") or []
    allowed = set(user.allowed_models or [])
    rules = super_agent.normalise_rules(policy.super_agent_rules)

    def offered(strict: bool) -> bool:
        return bool(super_agent.estimate(rules, catalogue, strict=strict, allowed=allowed))

    if mode == RoutingMode.super and project_id and policy.internal_data_strict_local:
        if offered(strict=True):
            return RoutingMode.super_strict
    if mode == RoutingMode.super_strict:
        return RoutingMode.super_strict if offered(strict=True) else RoutingMode.manual
    if offered(strict=False):
        return RoutingMode.super
    return RoutingMode.super_strict if offered(strict=True) else RoutingMode.manual


async def starting_mode(
    kind: SessionKind,
    user: User,
    *,
    project_id: str | None = None,
    fixed_model: str = "",
) -> tuple[RoutingMode, str]:
    """`(routing_mode, model)` of a new conversation nobody picked a mode for. `fixed_model`:
    the caller's or the agent's model (kept, `manual`)."""
    if kind not in ROUTED_KINDS:
        return RoutingMode.manual, fixed_model
    if fixed_model:
        return RoutingMode.manual, fixed_model
    pick = personal_pick(user, kind)
    if pick in (RoutingMode.super.value, RoutingMode.super_strict.value):
        return await usable_super(RoutingMode(pick), user, project_id=project_id), ""
    if pick:
        return RoutingMode.manual, pick
    start = await surface_start(kind)
    if start == RoutingMode.manual:
        return RoutingMode.manual, ""
    return await usable_super(start, user, project_id=project_id), ""
