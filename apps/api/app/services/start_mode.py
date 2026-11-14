"""How a new chat, report or slides conversation starts when the caller does not say.

Super Agent is always offered (owner decision 2026-11-14) and is the default start for the
three routed surfaces. The admin's per-surface default (설정 › 시스템 › 모델,
`settings_store.ROUTING_CHAT`) may instead be `super_strict` or a fixed model (`manual`).
A model fixed by the caller, by the conversation's agent or by the person's own home-screen
pick keeps the conversation on that model. A project conversation where internal material
stays strict-local starts on `super_strict`. When the chosen Super Agent entry has no model
this person may use, the conversation starts on the surface's model instead (no dead entry).
"""

from __future__ import annotations

from app.models.chat import RoutingMode, SessionKind
from app.models.user import User
from app.services import governance, settings_store, super_agent
from app.services import models as model_service

ROUTED_KINDS = frozenset({SessionKind.chat, SessionKind.report, SessionKind.slides})


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
    """`mode` (a Super Agent entry) as this conversation can run it: a project conversation
    under the strict-local rule moves `super` to `super_strict`; an entry with no usable model
    gives way to the other entry (`super` only to `super_strict`, never the reverse), else to
    `manual`."""
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
    if pick := personal_pick(user, kind):
        return RoutingMode.manual, pick
    start = await surface_start(kind)
    if start == RoutingMode.manual:
        return RoutingMode.manual, ""
    return await usable_super(start, user, project_id=project_id), ""
