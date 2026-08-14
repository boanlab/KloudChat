"""Organisation usage, computed from what actually happened.

Every figure comes from stored turns: `messages.usage.credits` is what the turn
was billed, `messages.model` what answered it, the session `kind` which surface
asked. Nothing is estimated or seeded.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request
from sqlalchemy import Integer, cast, func
from sqlmodel import col, select

from app.core.config import settings
from app.core.deps import AdminUser, CurrentUser, DbSession, client_ip
from app.models.chat import ChatSession, Message, Role
from app.models.governance import Governance
from app.models.user import ApiKey, AuditEvent, CreditLedger, User, UserStatus, utcnow
from app.schemas.admin import GovernanceIn
from app.services import governance, settings_store
from app.services import litellm as litellm_service

router = APIRouter(prefix="/admin", tags=["admin"])
#: The same numbers scoped to the caller. Its own router, so a forgotten admin
#: dependency cannot leak the whole instance.
me_router = APIRouter(prefix="/me", tags=["usage"])


def _cycle_start() -> datetime:
    """Midnight on the first of the current month — when allowances refill."""
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

#: `usage->>'credits'` as an integer. A turn whose upstream withheld usage
#: counts as zero rather than dropping the row, keeping request counts honest.
_CREDITS = func.coalesce(cast(Message.usage["credits"].astext, Integer), 0)


def _since(days: int) -> datetime:
    """Midnight `days-1` days ago, so a 7-day window is seven whole days."""
    start = datetime.now(UTC) - timedelta(days=days - 1)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/usage")
async def usage(admin: AdminUser, db: DbSession, days: int = Query(7, ge=1, le=90)):
    since = _since(days)
    # Assistant turns only: a user message is not a request to a model.
    answered = (Message.role == Role.assistant) & (Message.created_at >= since)

    totals = (
        await db.exec(
            select(
                func.coalesce(func.sum(_CREDITS), 0),
                func.count(),
                func.count(func.distinct(ChatSession.user_id)),
            )
            .select_from(Message)
            .join(ChatSession, col(Message.session_id) == col(ChatSession.id))
            .where(answered)
        )
    ).one()

    daily = (
        await db.exec(
            select(
                func.date_trunc("day", col(Message.created_at)).label("day"),
                func.coalesce(func.sum(_CREDITS), 0),
                func.count(),
            )
            .where(answered)
            .group_by("day")
            .order_by("day")
        )
    ).all()

    by_model = (
        await db.exec(
            select(
                Message.model,
                func.coalesce(func.sum(_CREDITS), 0),
                func.count(),
                func.count(func.distinct(ChatSession.user_id)),
            )
            .select_from(Message)
            .join(ChatSession, col(Message.session_id) == col(ChatSession.id))
            .where(answered, col(Message.model).is_not(None))
            .group_by(col(Message.model))
            .order_by(func.coalesce(func.sum(_CREDITS), 0).desc(), func.count().desc())
        )
    ).all()

    by_surface = (
        await db.exec(
            select(
                ChatSession.kind,
                func.coalesce(func.sum(_CREDITS), 0),
                func.count(),
            )
            .select_from(Message)
            .join(ChatSession, col(Message.session_id) == col(ChatSession.id))
            .where(answered)
            .group_by(col(ChatSession.kind))
            .order_by(func.coalesce(func.sum(_CREDITS), 0).desc())
        )
    ).all()

    top_users = (
        await db.exec(
            select(
                User.id,
                User.name,
                User.email,
                User.monthly_credits,
                func.coalesce(func.sum(_CREDITS), 0).label("spent"),
            )
            .select_from(Message)
            .join(ChatSession, col(Message.session_id) == col(ChatSession.id))
            .join(User, col(ChatSession.user_id) == col(User.id))
            .where(answered)
            .group_by(col(User.id), col(User.name), col(User.email), col(User.monthly_credits))
            .order_by(func.coalesce(func.sum(_CREDITS), 0).desc())
            .limit(10)
        )
    ).all()

    # Denominator for "against allocation": what the *active* roster may spend
    # this cycle. Rejected and pending accounts are excluded.
    allocated = (
        await db.exec(
            select(func.coalesce(func.sum(User.monthly_credits), 0)).where(
                User.status == UserStatus.active
            )
        )
    ).one()

    spent, requests, active_users = totals
    return {
        "days": days,
        "since": since,
        "totals": {
            "credits": int(spent),
            "requests": int(requests),
            "activeUsers": int(active_users),
            "allocatedCredits": int(allocated),
        },
        "daily": [
            {"date": day.date().isoformat(), "credits": int(c), "requests": int(n)}
            for day, c, n in daily
        ],
        "byModel": [
            {"model": m, "credits": int(c), "requests": int(n), "users": int(u)}
            for m, c, n, u in by_model
        ],
        "bySurface": [
            {"kind": k.value, "credits": int(c), "requests": int(n)} for k, c, n in by_surface
        ],
        "topUsers": [
            {
                "id": uid,
                "name": name,
                "email": email,
                "credits": int(spent_u),
                "allowance": int(allowance),
            }
            for uid, name, email, allowance, spent_u in top_users
        ],
    }


async def _api_key_spend(db: DbSession, user: User) -> list[dict]:
    """Spend per key the user issued. Empty when the proxy does not answer."""
    rows = (
        await db.exec(
            select(ApiKey)
            .where(ApiKey.user_id == user.id, col(ApiKey.revoked_at).is_(None))
            .order_by(col(ApiKey.created_at))
        )
    ).all()
    if not rows:
        return []

    # One request per key, issued concurrently. Ten keys is the ceiling, so
    # keeps the screen from waiting on a serial round trip.
    spends = await asyncio.gather(
        *(litellm_service.key_spend(settings_store.decrypt_secret(r.secret)) for r in rows),
        return_exceptions=True,
    )
    out = []
    for row, spend in zip(rows, spends, strict=True):
        if not isinstance(spend, dict):
            continue
        out.append({
            "id": row.id,
            "name": row.name,
            "preview": row.preview,
            "spendUsd": round(spend["spend"], 4),
            "credits": int(spend["spend"] * settings.credits_per_usd),
            "budgetUsd": spend["maxBudget"],
        })
    return out


@me_router.get("/usage")
async def my_usage(user: CurrentUser, db: DbSession, days: int = Query(30, ge=1, le=90)):
    """The caller's own spending, from the same stored turns as the admin view.

    Separate from `/admin/usage` rather than that endpoint with a filter: the
    figures an administrator needs — who spent it, how the roster compares — are
    exactly the ones a person must not be handed about everyone else. The shape
    here is what somebody can act on: what is left this month, where it went,
    and whether the trend will run out before the cycle does.
    """
    since = _since(days)
    mine = (
        (Message.role == Role.assistant)
        & (Message.created_at >= since)
        & (ChatSession.user_id == user.id)
    )
    joined = select().select_from(Message).join(
        ChatSession, col(Message.session_id) == col(ChatSession.id)
    )

    spent, requests = (
        await db.exec(
            joined.add_columns(func.coalesce(func.sum(_CREDITS), 0), func.count()).where(mine)
        )
    ).one()

    daily = (
        await db.exec(
            joined.add_columns(
                func.date_trunc("day", col(Message.created_at)).label("day"),
                func.coalesce(func.sum(_CREDITS), 0),
                func.count(),
            )
            .where(mine)
            .group_by("day")
            .order_by("day")
        )
    ).all()

    by_model = (
        await db.exec(
            joined.add_columns(
                Message.model,
                func.coalesce(func.sum(_CREDITS), 0),
                func.count(),
            )
            .where(mine, col(Message.model).is_not(None))
            .group_by(col(Message.model))
            .order_by(func.coalesce(func.sum(_CREDITS), 0).desc())
        )
    ).all()

    by_surface = (
        await db.exec(
            joined.add_columns(
                ChatSession.kind,
                func.coalesce(func.sum(_CREDITS), 0),
                func.count(),
            )
            .where(mine)
            .group_by(col(ChatSession.kind))
            .order_by(func.coalesce(func.sum(_CREDITS), 0).desc())
        )
    ).all()

    # Totals come from the ledger, not from conversation turns.
    #
    # Conversation is not the only thing that spends — image generation bills
    # without producing a message. The ledger is what the balance is computed
    # from, so the screen has to agree with the ledger.
    #
    # The breakdown below is per model and per surface, which the ledger does
    # not record. It is derived from messages and therefore covers turns only;
    # the difference is surfaced as `other`.
    spend = -func.coalesce(func.sum(CreditLedger.delta), 0)
    ledger_window = (
        await db.exec(
            select(spend)
            .where(CreditLedger.user_id == user.id)
            .where(CreditLedger.delta < 0)
            .where(CreditLedger.created_at >= since)
        )
    ).one()
    cycle_used = (
        await db.exec(
            select(spend)
            .where(CreditLedger.user_id == user.id)
            .where(CreditLedger.delta < 0)
            .where(CreditLedger.created_at >= _cycle_start())
        )
    ).one()
    # SQLModel hands back a scalar for some drivers and a one-element row for
    # others; both mean the same number here.
    ledger_window = int(
        ledger_window if not hasattr(ledger_window, "__len__") else ledger_window[0]
    )
    cycle_used = int(cycle_used if not hasattr(cycle_used, "__len__") else cycle_used[0])

    return {
        "days": days,
        "since": since,
        # What external tools spent through issued keys. Shown beside
        # conversation usage rather than added to it: the first number comes
        # from kchat's ledger and this one from the proxy, and they are
        # aggregated at different moments and in different units — adding them
        # produces a figure that matches neither.
        "apiKeys": await _api_key_spend(db, user),
        "totals": {
            "credits": ledger_window,
            "requests": int(requests),
            # Spend the per-model breakdown cannot account for — today that is
            # image generation, which bills without producing a turn.
            "otherCredits": max(0, ledger_window - int(spent)),
        },
        "cycle": {
            "allowance": int(user.monthly_credits or 0),
            "used": int(cycle_used),
            "remaining": max(0, int(user.monthly_credits or 0) - int(cycle_used)),
        },
        "daily": [
            {"date": day.date().isoformat(), "credits": int(c), "requests": int(n)}
            for day, c, n in daily
        ],
        "byModel": [
            {"model": m, "credits": int(c), "requests": int(n)} for m, c, n in by_model
        ],
        "bySurface": [
            {"kind": k.value, "credits": int(c), "requests": int(n)} for k, c, n in by_surface
        ],
    }


@router.get("/audit")
async def audit_log(
    admin: AdminUser,
    db: DbSession,
    limit: int = Query(100, ge=1, le=500),
    severity: str | None = None,
):
    """The real trail: what the auth and admin routes wrote as they ran."""
    from app.models.user import AuditEvent

    query = select(AuditEvent).order_by(col(AuditEvent.at).desc()).limit(limit)
    if severity:
        query = query.where(AuditEvent.severity == severity)
    rows = (await db.exec(query)).all()

    actor_ids = {r.actor_id for r in rows if r.actor_id}
    actors: dict[str, str] = {}
    if actor_ids:
        actors = {
            u.id: u.email
            for u in (await db.exec(select(User).where(col(User.id).in_(actor_ids)))).all()
        }

    return [
        {
            "id": r.id,
            "at": r.at,
            "actor": actors.get(r.actor_id or "", r.actor_id or "시스템"),
            "action": r.action,
            "target": r.target,
            "detail": r.detail,
            "ip": r.ip,
            "severity": r.severity,
        }
        for r in rows
    ]


@router.get("/governance")
async def get_governance(admin: AdminUser, db: DbSession):
    policy = await db.get(Governance, "default")
    policy = policy or Governance()
    return {
        "piiMasking": policy.pii_masking,
        "intentFilter": policy.intent_filter,
        "blockedCategories": list(policy.blocked_categories or []),
        "retentionDays": policy.retention_days,
    }


@router.put("/governance")
async def put_governance(
    payload: GovernanceIn, request: Request, admin: AdminUser, db: DbSession
):
    """Writes the policy and applies the part that acts on what is already stored.

    Retention is the one rule that is retroactive: shortening the window has to
    reach back, or the setting would only govern messages sent after it changed.
    """
    policy = await db.get(Governance, "default") or Governance(id="default")
    patch = payload.model_dump(exclude_unset=True)
    for field, value in patch.items():
        setattr(policy, field, value)
    policy.updated_at = utcnow()
    policy.updated_by = admin.id
    db.add(policy)
    db.add(
        AuditEvent(
            actor_id=admin.id,
            action="governance.update",
            target="정책",
            detail=", ".join(sorted(patch)),
            ip=client_ip(request),
        )
    )
    await db.commit()
    governance.invalidate()

    cleared = await governance.sweep_expired(db)
    await db.commit()
    return {"ok": True, "clearedMessages": cleared}
