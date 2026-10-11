"""Credit allowances, the monthly cycle, and the append-only ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from zoneinfo import ZoneInfo

from sqlalchemy import event, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import CreditLedger, User, UserStatus, utcnow

KST = ZoneInfo("Asia/Seoul")


def next_cycle_reset(after: datetime | None = None) -> datetime:
    """Next month's first day 00:00 KST, as UTC."""
    now = (after or utcnow()).astimezone(KST)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=KST).astimezone(UTC)


def cycle_start(at: datetime | None = None) -> datetime:
    """Current month's first day 00:00 KST, as UTC; the usage screens' only month boundary."""
    now = (at or utcnow()).astimezone(KST)
    return datetime(now.year, now.month, 1, tzinfo=KST).astimezone(UTC)


def grant_initial_allowance(db: AsyncSession, user: User, monthly_credits: int) -> None:
    """Set allowance and open the first cycle. Caller commits."""
    user.monthly_credits = monthly_credits
    user.credits_used = 0
    user.cycle_resets_at = next_cycle_reset()
    db.add(user)
    db.add(
        CreditLedger(
            user_id=user.id,
            delta=monthly_credits,
            reason="allowance.grant",
        )
    )


def set_allowance(db: AsyncSession, user: User, monthly_credits: int) -> None:
    """Change allowance mid-cycle; `credits_used` untouched (may exceed it)."""
    delta = monthly_credits - user.monthly_credits
    user.monthly_credits = monthly_credits
    if user.cycle_resets_at is None:
        user.cycle_resets_at = next_cycle_reset()
    db.add(user)
    if delta:
        db.add(CreditLedger(user_id=user.id, delta=delta, reason="allowance.set"))


def charge_for_tokens(model: dict, input_tokens: int, output_tokens: int) -> int:
    """Credits for one turn: per-1k in/out rates, rounded up, never zero when priced."""
    per_in = model.get("inputCreditCost") or 0
    per_out = model.get("creditCost") or 0
    if per_in == 0 and per_out == 0:
        return 0  # self-hosted
    exact = (input_tokens * per_in + output_tokens * per_out) / 1000
    return max(1, ceil(exact))


def has_headroom(user: User, model: dict) -> bool:
    """Pre-flight: one 1k-output turn's worth left."""
    per_out = model.get("creditCost") or 0
    if per_out == 0:
        return True
    return user.credits_remaining >= per_out


#: surface by `reason` prefix; `document.*` omitted (both surfaces), callers pass `surface`
_SURFACE_OF_REASON = {
    "chat": "chat",
    "report": "report",
    "page": "report",  # HTML document track
    "deck": "slides",
    "image": "image",
    "audio": "av",
    "video": "av",
}


def surface_for(reason: str) -> str | None:
    """Surface implied by `reason`, or `None`."""
    return _SURFACE_OF_REASON.get(reason.split(".", 1)[0])


def settle(
    db: AsyncSession,
    user: User,
    credits: int,
    *,
    reason: str,
    session_id: str | None = None,
    model: str | None = None,
    surface: str | None = None,
    publication_id: str | None = None,
) -> None:
    """Deduct on completion; may overshoot. Caller commits."""
    if credits <= 0:
        return
    surface = surface or surface_for(reason)
    # atomic SQL increment queued for flush (`_apply_credit_deltas`): concurrent turns both land
    info = getattr(getattr(db, "sync_session", db), "info", None)
    if isinstance(info, dict):
        info.setdefault(_DELTAS, []).append((user.id, credits))
    before = int(user.credits_used or 0)
    set_committed_value(user, "credits_used", before + credits)
    db.add(
        CreditLedger(
            user_id=user.id,
            delta=-credits,
            reason=reason,
            session_id=session_id,
            model=model,
            surface=surface,
            publication_id=publication_id,
        )
    )


_DELTAS = "kchat_credit_deltas"


@event.listens_for(Session, "before_flush")
def _apply_credit_deltas(session: Session, _context, _instances) -> None:
    """Write queued `settle` charges as atomic increments."""
    for user_id, credits in session.info.pop(_DELTAS, []):
        session.execute(
            update(User)
            .where(User.id == user_id)  # type: ignore[arg-type]
            .values(credits_used=User.credits_used + credits)
        )


#: web search ledger reason: zero credits, `units` searches
SEARCH_REASON = "search.web"


def record_units(
    db: AsyncSession,
    user: User,
    *,
    reason: str,
    model: str | None,
    units: int,
    unit: str,
    session_id: str | None = None,
    surface: str | None = None,
    publication_id: str | None = None,
) -> None:
    """Zero-delta ledger row for free unit-measured work. Caller commits."""
    if units <= 0:
        return
    db.add(
        CreditLedger(
            user_id=user.id,
            delta=0,
            reason=reason,
            session_id=session_id,
            model=model,
            surface=surface or surface_for(reason),
            units=units,
            unit=unit,
            publication_id=publication_id,
        )
    )


def record_searches(
    db: AsyncSession,
    user: User,
    searches: int,
    *,
    session_id: str | None = None,
    surface: str | None = None,
    publication_id: str | None = None,
) -> None:
    """Per-turn web search ledger row for quota tracking; free. Caller commits."""
    record_units(
        db,
        user,
        reason=SEARCH_REASON,
        model=None,
        units=searches,
        unit="searches",
        session_id=session_id,
        surface=surface,
        publication_id=publication_id,
    )


def refill_if_due(db: AsyncSession, user: User, now: datetime | None = None) -> bool:
    """Reset one account's cycle if due; idempotent. Caller commits."""
    now = now or utcnow()
    if user.cycle_resets_at is None or user.cycle_resets_at > now:
        return False
    # reset, not top-up: unused credits expire
    db.add(
        CreditLedger(
            user_id=user.id,
            delta=user.monthly_credits,
            reason="allowance.refill",
        )
    )
    user.credits_used = 0
    user.cycle_resets_at = next_cycle_reset(now)
    db.add(user)
    return True


async def refill_due(db: AsyncSession, now: datetime | None = None) -> int:
    """Reset every due cycle (admin/cron); refilled user count."""
    now = now or utcnow()
    users = (
        await db.exec(
            select(User).where(
                User.status == UserStatus.active,
                User.cycle_resets_at.is_not(None),
                User.cycle_resets_at <= now,
            )
        )
    ).all()

    refilled = sum(1 for user in users if refill_if_due(db, user, now))
    await db.commit()
    return refilled
