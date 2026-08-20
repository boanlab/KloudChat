"""Credit allowances and the append-only ledger.

Assignment at approval, the monthly reset, and the debits and refunds that
follow actual spend. The balance is always derived from the ledger — never
stored and updated in place, so a lost write can shift a number but cannot
invent one.
"""

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
    """Changes the allowance mid-cycle without touching what was already spent.

    Lowering it below `credits_used` is allowed: nothing is left until the next
    refill. Zeroing the cycle would hide real usage from the ledger.
    """
    delta = monthly_credits - user.monthly_credits
    user.monthly_credits = monthly_credits
    if user.cycle_resets_at is None:
        user.cycle_resets_at = next_cycle_reset()
    db.add(user)
    if delta:
        db.add(CreditLedger(user_id=user.id, delta=delta, reason="allowance.set"))


def charge_for_tokens(model: dict, input_tokens: int, output_tokens: int) -> int:
    """Credits for one completed turn.

    Both directions are billed: `creditCost` is the headline per-1k-output rate,
    but a long context is where the money goes.

    Rounded up, and never zero for a priced model.
    """
    per_in = model.get("inputCreditCost") or 0
    per_out = model.get("creditCost") or 0
    if per_in == 0 and per_out == 0:
        return 0  # self-hosted
    exact = (input_tokens * per_in + output_tokens * per_out) / 1000
    return max(1, ceil(exact))


def has_headroom(user: User, model: dict) -> bool:
    """Pre-flight check.

    A turn's cost is unknowable before it runs, so the bar is one 1k-output
    turn's worth — enough to stop an account at zero from starting.
    """
    per_out = model.get("creditCost") or 0
    if per_out == 0:
        return True
    return user.credits_remaining >= per_out


def settle(
    db: AsyncSession,
    user: User,
    credits: int,
    *,
    reason: str,
    session_id: str | None = None,
    model: str | None = None,
) -> None:
    """Deducts on completion. Caller commits.

    Allowed to overshoot: the tokens were already spent upstream, and the next
    request is refused by `has_headroom`.

    Pass `model` wherever one model earned the charge — it is what lets the
    usage screens say where the money went instead of filing it under "other".
    Leave it out when nothing single is true: a comparison bills several models
    on one row, and a design extraction belongs to no conversation at all.
    """
    if credits <= 0:
        return
    user.credits_used += credits
    db.add(user)
    db.add(
        CreditLedger(
            user_id=user.id,
            delta=-credits,
            reason=reason,
            session_id=session_id,
            model=model,
        )
    )


async def refill_due(db: AsyncSession, now: datetime | None = None) -> int:
    """Resets every cycle that has come due. Returns how many users were refilled.

    For a daily cron. Idempotent within a cycle: once `cycle_resets_at` moves
    forward, a second run the same day is a no-op.
    """
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

    for user in users:
        # A refill is a reset, not a top-up: unused credits expire.
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

    await db.commit()
    return len(users)
