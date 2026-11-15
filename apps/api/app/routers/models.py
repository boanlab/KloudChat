"""Model catalogue and the caller's credit balance. Active accounts only."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.services import adaptive_routing, governance, super_agent
from app.services import models as model_service
from app.services.credits import refill_due

router = APIRouter(tags=["models"])


async def _catalogue_for_user(user: CurrentUser, *, force: bool = False):
    """Allowlist-filtered catalogue plus auto-routing availability."""
    catalogue = (
        await model_service.list_models(force=True) if force else await model_service.list_models()
    )
    allowed = set(user.allowed_models or [])
    visible = [m for m in catalogue["models"] if not allowed or m["id"] in allowed]
    policy = await governance.current()
    by_id = {model["id"]: model for model in catalogue["models"]}
    classifier_id = policy.adaptive_classifier_model_id
    classifier_ok = adaptive_routing.classifier_is_usable(
        by_id.get(classifier_id or ""), allowed_model_ids=allowed
    )
    economy_ids = [
        model_id
        for model_id in list(policy.adaptive_economy_model_ids or [])[:3]
        if adaptive_routing.economy_is_baseline_usable(
            by_id.get(model_id), allowed_model_ids=allowed
        )
    ]
    # coarse check; per-turn decision in `quality_candidates`
    quality_ids = [
        model_id
        for model_id in list(policy.adaptive_quality_model_ids or [])[:3]
        if model_id in by_id
        and (not allowed or model_id in allowed)
        and "chat" in by_id[model_id].get("kinds", [])
    ]
    if not policy.adaptive_routing_enabled:
        reason = "disabled"
    elif not classifier_ok:
        reason = "classifier_unavailable"
    elif not economy_ids:
        reason = "no_economy_models"
    else:
        reason = None
    return {
        **catalogue,
        "models": visible,
        "autoRouting": {
            "enabled": policy.adaptive_routing_enabled,
            "available": bool(policy.adaptive_routing_enabled and classifier_ok and economy_ids),
            "reason": reason,
            "classifierModelId": classifier_id if classifier_ok else None,
            "economyModelIds": economy_ids,
            # upgrade lane: shared classifier, own candidate list
            "qualityAvailable": bool(
                policy.adaptive_quality_enabled and classifier_ok and quality_ids
            ),
            "qualityReason": (
                "disabled"
                if not policy.adaptive_quality_enabled
                else "classifier_unavailable"
                if not classifier_ok
                else "no_quality_models"
                if not quality_ids
                else None
            ),
            "qualityModelIds": quality_ids,
        },
        "superAgent": _super_agent_offer(policy, catalogue["models"], allowed),
    }


def _super_agent_offer(policy, catalogue: list[dict], allowed: set[str]) -> dict:
    """The picker's two Super Agent entries (always offered): usable or why not, with a
    typical turn's credits (min–max across the class table)."""
    rules = super_agent.normalise_rules(policy.super_agent_rules)
    # a project conversation keeps internal material strict-local: its `super` start is
    # `super_strict` (`start_mode.usable_super`); the picker shows that before the first send
    offer: dict = {"projectStrict": bool(policy.internal_data_strict_local)}
    for key, strict in (("default", False), ("strict", True)):
        estimate = super_agent.estimate(rules, catalogue, strict=strict, allowed=allowed)
        offer[key] = {
            "available": bool(estimate),
            "reason": None if estimate else "no_models",
            "estimate": estimate,
        }
    return offer


@router.get("/models")
async def list_models(user: CurrentUser):
    """Allowlist-filtered catalogue (proxy enforces; this trims the picker)."""
    return await _catalogue_for_user(user)


@router.post("/models/refresh")
async def refresh_models(admin: AdminUser):
    """Drops the catalogue cache and re-reads LiteLLM."""
    model_service.invalidate_cache()
    return await _catalogue_for_user(admin, force=True)


@router.get("/credits")
async def credits(user: CurrentUser):
    return {
        "monthlyCredits": user.monthly_credits,
        "creditsUsed": user.credits_used,
        "creditsRemaining": user.credits_remaining,
        "cycleResetsAt": user.cycle_resets_at,
    }


@router.post("/credits/refill")
async def run_refill(admin: AdminUser, db: DbSession):
    """Manual trigger for the monthly credit reset."""
    count = await refill_due(db)
    return {"refilled": count}
