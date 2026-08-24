"""Model catalogue and the caller's credit balance.

Both are gated behind an active account: a pending user has no allowance yet, so
showing them a priced model list would only invite the question of why nothing
works.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.services import adaptive_routing, governance
from app.services import models as model_service
from app.services.credits import refill_due

router = APIRouter(tags=["models"])


async def _catalogue_for_user(user: CurrentUser, *, force: bool = False):
    """Shape both catalogue endpoints with the same user-scoped Auto contract."""
    catalogue = (
        await model_service.list_models(force=True)
        if force
        else await model_service.list_models()
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
    # Coarse on purpose. Whether an upgrade candidate is usable also depends on
    # the model the person is currently on — an upgrade may not send a turn
    # further than that model already does — and this endpoint serves a
    # catalogue, not a turn. The per-turn check in `quality_candidates` is the
    # one that decides; this only says whether an administrator set the lane up.
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
            # The upgrade lane shares the classifier and the on/off switch; only
            # the candidate list is its own.
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
    }


@router.get("/models")
async def list_models(user: CurrentUser):
    """The catalogue this account may actually use.

    Filtered here as well as on the proxy: the proxy is what makes the limit
    real, but a picker offering models that answer 401 is a worse way to learn
    about a restriction than simply not seeing them.
    """
    return await _catalogue_for_user(user)


@router.post("/models/refresh")
async def refresh_models(admin: AdminUser):
    """Drops the 30-second cache. For when an operator has just edited
    `litellm-config.yaml` and does not want to wait for it to age out.
    """
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
    """Manual trigger for the monthly reset. A daily cron calls the same service;
    this exists so an operator can prove it works without waiting for the 1st.
    """
    count = await refill_due(db)
    return {"refilled": count}
