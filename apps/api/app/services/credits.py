"""Credit allowances, the monthly cycle, and the append-only ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from zoneinfo import ZoneInfo

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import CreditLedger, User, UserStatus, utcnow

KST = ZoneInfo("Asia/Seoul")


def next_cycle_reset(after: datetime | None = None) -> datetime:
    """First day of the next month at 00:00 KST, stored as UTC."""
    now = (after or utcnow()).astimezone(KST)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=KST).astimezone(UTC)


def cycle_start(at: datetime | None = None) -> datetime:
    """First day of the current month at 00:00 KST, as UTC. The only month boundary the usage
    screens use.
    """
    now = (at or utcnow()).astimezone(KST)
    return datetime(now.year, now.month, 1, tzinfo=KST).astimezone(UTC)


def grant_initial_allowance(db: AsyncSession, user: User, monthly_credits: int) -> None:
    """Sets the allowance and opens the first cycle. Caller commits."""
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
    """Changes the allowance mid-cycle; `credits_used` is untouched, so it may exceed the new
    allowance.
    """
    delta = monthly_credits - user.monthly_credits
    user.monthly_credits = monthly_credits
    if user.cycle_resets_at is None:
        user.cycle_resets_at = next_cycle_reset()
    db.add(user)
    if delta:
        db.add(CreditLedger(user_id=user.id, delta=delta, reason="allowance.set"))


def charge_for_tokens(model: dict, input_tokens: int, output_tokens: int) -> int:
    """Credits for one turn: per-1k rates for input and output, rounded up, never zero for a priced
    model.
    """
    per_in = model.get("inputCreditCost") or 0
    per_out = model.get("creditCost") or 0
    if per_in == 0 and per_out == 0:
        return 0  # self-hosted
    exact = (input_tokens * per_in + output_tokens * per_out) / 1000
    return max(1, ceil(exact))


def has_headroom(user: User, model: dict) -> bool:
    """Pre-flight check: at least one 1k-output turn's worth remains."""
    per_out = model.get("creditCost") or 0
    if per_out == 0:
        return True
    return user.credits_remaining >= per_out


#: Surface by the first segment of a `reason` (`deck.generate` → slides).
#: `document.*` is absent on purpose: it happens on both document surfaces, so
#: those callers pass `surface` explicitly.
_SURFACE_OF_REASON = {
    "chat": "chat",
    "report": "report",
    "page": "report",  # the HTML document track
    "deck": "slides",
    "image": "image",
    "audio": "av",
    "video": "av",
}


def surface_for(reason: str) -> str | None:
    """The surface a reason is spoken from, or `None` when it does not say."""
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
) -> None:
    """Deducts on completion; may overshoot. Caller commits.

    `model` when one model earned the charge; `surface` when `reason` does not
    say (`document.*`), else derived from it.
    """
    if credits <= 0:
        return
    surface = surface or surface_for(reason)
    user.credits_used += credits
    db.add(user)
    db.add(
        CreditLedger(
            user_id=user.id,
            delta=-credits,
            reason=reason,
            session_id=session_id,
            model=model,
            surface=surface,
        )
    )


def record_units(
    db: AsyncSession,
    user: User,
    *,
    reason: str,
    model: str,
    units: int,
    unit: str,
    session_id: str | None = None,
    surface: str | None = None,
) -> None:
    """A zero-delta ledger row for free work measured in units (seconds, chunks). Caller commits."""
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
        )
    )


def refill_if_due(db: AsyncSession, user: User, now: datetime | None = None) -> bool:
    """Resets one account's cycle if due; idempotent. Called wherever the user row is loaded. Caller
    commits.
    """
    now = now or utcnow()
    if user.cycle_resets_at is None or user.cycle_resets_at > now:
        return False
    # A reset, not a top-up: unused credits expire.
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
    """Resets every due cycle (admin button / cron sweep). Returns how many users were refilled."""
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
