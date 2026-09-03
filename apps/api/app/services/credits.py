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


def cycle_start(at: datetime | None = None) -> datetime:
    """When the cycle in progress began — 00:00 KST on the first, as UTC.

    The counterpart of `next_cycle_reset`, and the only definition of "이번 달"
    the usage screens may use. Read as a UTC month boundary instead, the two
    disagree for the nine hours between 15:00 UTC on the last of the month and
    midnight UTC on the first: the allowance has already refilled, and the
    screen is still totalling the month that ended.
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


#: Which surface a `reason` is spoken from, where the word itself says so.
#:
#: Every charge already names what it was for — `deck.generate`,
#: `report.factcheck`, `chat.title` — and that word is the surface in all but a
#: couple of cases. Filling `surface` from it means a caller has to remember
#: only where the word is ambiguous, instead of at eleven call sites where
#: forgetting was silent: the spend still landed, unattributed, and the usage
#: screen showed 31,741 credits under 기타 with no requests beside them.
#:
#: `document.*` is deliberately absent — a plan or a revision happens on both
#: document surfaces and the word does not say which, so those pass it.
_SURFACE_OF_REASON = {
    "chat": "chat",
    "report": "report",
    # The HTML document track. Its artifacts are documents, and 보고서 is the
    # surface they are made and read on.
    "page": "report",
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
    """Deducts on completion. Caller commits.

    Allowed to overshoot: the tokens were already spent upstream, and the next
    request is refused by `has_headroom`.

    Pass `model` wherever one model earned the charge — it is what lets the
    usage screens say where the money went instead of filing it under "other".
    Leave it out when nothing single is true: a comparison bills several models
    on one row, and a design extraction belongs to no conversation at all.

    `surface` is the same bargain for the other axis. Read off the row rather
    than through `session_id`, so a deleted conversation does not take its
    spend into 기타 with it — and filled in from `reason` when it was not
    given, because the reason almost always says. Pass it explicitly where the
    word is ambiguous: `document.plan` and `document.revise` happen on both
    document surfaces.
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
    """A row for work that cost no credits — seconds of speech, chunks embedded.

    `settle` writes nothing for a free model, and so Whisper and the embedding
    model never appeared on the usage screens: the work happened, nobody could
    see how much. Caller commits.
    """
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
    """Resets one account's cycle if it has come due. Caller commits.

    A cycle that has come due is not a note to run something later. As far as
    the account is concerned the allowance has refilled, and until the row says
    so, `has_headroom` goes on refusing turns there are credits for and the
    sidebar goes on counting down a month that ended. `refill_due` below says
    it is for a daily cron, and nothing in this deployment was that cron — so
    an account that spent its August allowance met September with nothing.

    Doing it where the user row is loaded makes the reset self-healing: it
    lands on that account's first request of the new cycle, whatever the
    request was. Idempotent — `cycle_resets_at` moves forward, so the next call
    is a no-op.
    """
    now = now or utcnow()
    if user.cycle_resets_at is None or user.cycle_resets_at > now:
        return False
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
    return True


async def refill_due(db: AsyncSession, now: datetime | None = None) -> int:
    """Resets every cycle that has come due. Returns how many users were refilled.

    Kept for the administrator's button and for a cron that wants to sweep the
    whole roster at once; `refill_if_due` is what actually reaches most
    accounts, on their own next request.
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

    refilled = sum(1 for user in users if refill_if_due(db, user, now))
    await db.commit()
    return refilled
