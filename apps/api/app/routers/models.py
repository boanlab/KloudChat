"""Model catalogue and the caller's credit balance.

Both are gated behind an active account: a pending user has no allowance yet, so
showing them a priced model list would only invite the question of why nothing
works.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.services import models as model_service
from app.services.credits import refill_due

router = APIRouter(tags=["models"])


@router.get("/models")
async def list_models(user: CurrentUser):
    """The catalogue this account may actually use.

    Filtered here as well as on the proxy: the proxy is what makes the limit
    real, but a picker offering models that answer 401 is a worse way to learn
    about a restriction than simply not seeing them.
    """
    catalogue = await model_service.list_models()
    allowed = set(user.allowed_models or [])
    if not allowed:
        return catalogue
    return {
        **catalogue,
        "models": [m for m in catalogue["models"] if m["id"] in allowed],
    }


@router.post("/models/refresh")
async def refresh_models(admin: AdminUser):
    """Drops the 30-second cache. For when an operator has just edited
    `litellm-config.yaml` and does not want to wait for it to age out.
    """
    model_service.invalidate_cache()
    return await model_service.list_models(force=True)


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
