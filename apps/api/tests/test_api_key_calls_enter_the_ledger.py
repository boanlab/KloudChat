"""Calls made with a member's own API key reach the ledger, the month's balance and the usage
screens once each, at this instance's rates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import col, select
from test_usage_comes_from_the_ledger import _account, db  # noqa: F401 — fixture

from app.models.user import ApiKey, CreditLedger, User
from app.routers import usage as usage_router
from app.services import api_key_usage
from app.services import credits as credits_service

_CATALOG = {
    "models": [
        {"id": "openai/gpt-5-mini", "inputCreditCost": 10, "creditCost": 40},
        {"id": "local/qwen3.8-27b", "inputCreditCost": 0, "creditCost": 0},
    ]
}


def _log(ref: str, key: str, *, model: str, tokens_in: int, tokens_out: int, at: datetime,
         status: str = "success") -> dict:
    return {
        "request_id": ref,
        "api_key": api_key_usage.key_hash(key),
        "model_group": model,
        "prompt_tokens": tokens_in,
        "completion_tokens": tokens_out,
        "startTime": at.isoformat(),
        "status": status,
    }


@pytest.fixture
def proxy(monkeypatch):
    """The proxy's spend log, filtered as `/spend/logs/v2` does."""
    rows: list[dict] = []
    asked: list[tuple[datetime, datetime]] = []

    async def _spend_logs(hashed, start, end):
        asked.append((start, end))
        return [r for r in rows if r["api_key"] == hashed]

    async def _models(force=False):
        return _CATALOG

    async def _no_spend(_secret):
        return None

    monkeypatch.setattr(api_key_usage, "spend_logs", _spend_logs)
    monkeypatch.setattr(api_key_usage.models_service, "list_models", _models)
    monkeypatch.setattr(api_key_usage.settings_store, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(usage_router.settings_store, "decrypt_secret", lambda value: value)
    monkeypatch.setattr(usage_router.litellm_service, "key_spend", _no_spend)
    return rows, asked


async def _key(db, user: User, secret: str) -> None:  # noqa: F811
    db.add(ApiKey(user_id=user.id, name="스크립트", secret=secret, preview=secret[-4:]))
    await db.commit()


@pytest.mark.asyncio
async def test_each_call_is_one_row_charged_at_the_instance_rates(db, proxy):  # noqa: F811
    rows, _ = proxy
    user = await _account(db)
    await _key(db, user, "sk-member-one")
    now = datetime.now(UTC)
    rows += [
        _log("r1", "sk-member-one", model="openai/gpt-5-mini", tokens_in=2000, tokens_out=1000,
             at=now - timedelta(minutes=20)),
        _log("r2", "sk-member-one", model="local/qwen3.8-27b", tokens_in=500, tokens_out=300,
             at=now - timedelta(minutes=10)),
        # failed call: nothing to count
        _log("r3", "sk-member-one", model="openai/gpt-5-mini", tokens_in=10, tokens_out=0,
             at=now - timedelta(minutes=5), status="failure"),
        # someone else's key (or KloudChat's own): not this sync's
        _log("r4", "sk-not-a-member-key", model="openai/gpt-5-mini", tokens_in=9000,
             tokens_out=9000, at=now - timedelta(minutes=5)),
    ]

    assert await api_key_usage.sync(db, now=now) == 2
    # a second pass over the same log writes nothing again
    assert await api_key_usage.sync(db, now=now + timedelta(minutes=5)) == 0

    ledger = (
        await db.exec(select(CreditLedger).where(CreditLedger.reason == "api.key")
                      .order_by(col(CreditLedger.created_at)))
    ).all()
    assert [(r.external_ref, r.model, r.delta, r.units, r.surface) for r in ledger] == [
        ("r1", "openai/gpt-5-mini", -60, 3000, "api"),  # 2k×10 + 1k×40
        ("r2", "local/qwen3.8-27b", 0, 800, "api"),
    ]
    await db.refresh(user)
    assert user.credits_used == 60

    report = await usage_router.my_usage(user, db, days=30)
    assert report["totals"]["credits"] == 60
    assert report["totals"]["requests"] == 2
    assert {row["kind"]: (row["credits"], row["requests"]) for row in report["bySurface"]} == {
        "api": (60, 2)
    }
    assert {row["model"]: row["requests"] for row in report["byModel"]} == {
        "openai/gpt-5-mini": 1,
        "local/qwen3.8-27b": 1,
    }


@pytest.mark.asyncio
async def test_a_revoked_key_still_counts_and_reading_resumes_near_the_last_row(db, proxy):  # noqa: F811
    rows, asked = proxy
    user = await _account(db)
    await _key(db, user, "sk-revoked")
    key = (await db.exec(select(ApiKey))).one()
    key.revoked_at = datetime.now(UTC)
    db.add(key)
    await db.commit()
    now = datetime.now(UTC)
    rows.append(_log("r1", "sk-revoked", model="openai/gpt-5-mini", tokens_in=1000, tokens_out=0,
                     at=now - timedelta(hours=2)))

    assert await api_key_usage.sync(db, now=now) == 1
    # first pass reads from the start of the month
    assert asked[0][0] == credits_service.cycle_start(now)

    later = now + timedelta(hours=1)
    await api_key_usage.sync(db, now=later)
    # then from shortly before the newest row it wrote
    assert asked[-1][0] == (now - timedelta(hours=2)) - timedelta(minutes=30)


@pytest.mark.asyncio
async def test_a_call_from_last_month_is_kept_but_not_charged_to_this_month(db, proxy):  # noqa: F811
    rows, _ = proxy
    user = await _account(db)
    await _key(db, user, "sk-member-two")
    now = datetime.now(UTC)
    cycle = credits_service.cycle_start(now)
    # a pass already ran last month: reading resumes before the month turned
    db.add(CreditLedger(user_id=user.id, delta=0, reason="api.key", units=1, unit="tokens",
                        external_ref="old", created_at=cycle - timedelta(minutes=5)))
    await db.commit()
    rows.append(_log("late", "sk-member-two", model="openai/gpt-5-mini", tokens_in=1000,
                     tokens_out=0, at=cycle - timedelta(minutes=1)))

    assert await api_key_usage.sync(db, now=now) == 1
    await db.refresh(user)
    assert user.credits_used == 0
