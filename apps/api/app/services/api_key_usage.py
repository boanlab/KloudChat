"""Member-held API key calls (설정 › API 키), read from the proxy's spend log into the ledger.

Those calls go to the proxy directly, never through a turn here, so nothing else charges or
counts them. Each successful request becomes one ledger row: the key holder, the model, its
tokens as `units`, and credits at this instance's rates, with the request id as `external_ref`
(unique). The proxy's own user ceiling still applies alongside.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, text, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import SessionLocal
from app.models.user import ApiKey, CreditLedger, User
from app.services import credits as credits_service
from app.services import models as models_service
from app.services import settings_store

log = logging.getLogger(__name__)

API_KEY_REASON = "api.key"
API_SURFACE = "api"

_INTERVAL = 300
#: spend log rows land shortly after the call
_LAG = timedelta(minutes=1)
#: re-read window before the newest row; `external_ref` drops repeats
_OVERLAP = timedelta(minutes=30)
_PAGE = 100
_MAX_PAGES = 50
#: pg_advisory_xact_lock key: one replica syncs at a time
_LOCK_KEY = 7342


def key_hash(secret: str) -> str:
    """The proxy's stored form of a key (spend log `api_key`)."""
    return hashlib.sha256(secret.encode()).hexdigest()


def _when(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def spend_logs(hashed: str, start: datetime, end: datetime) -> list[dict] | None:
    """One key's spend log rows in [start, end), or None if the proxy is unreadable."""
    base, key = await settings_store.litellm_config()
    if not base or not key:
        return None
    fmt = "%Y-%m-%d %H:%M:%S"
    rows: list[dict] = []
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {key}"},
        timeout=httpx.Timeout(15.0, connect=4.0),
    ) as client:
        for page in range(1, _MAX_PAGES + 1):
            try:
                response = await client.get(
                    "/spend/logs/v2",
                    params={
                        "api_key": hashed,
                        "start_date": start.strftime(fmt),
                        "end_date": end.strftime(fmt),
                        "page": page,
                        "page_size": _PAGE,
                        "sort_by": "startTime",
                        "sort_order": "asc",
                    },
                )
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.info("api key spend log unreadable: %s", type(exc).__name__)
                return None
            data = body.get("data") if isinstance(body, dict) else body
            if not isinstance(data, list):
                break
            rows += [r for r in data if isinstance(r, dict)]
            total_pages = body.get("total_pages") if isinstance(body, dict) else None
            if len(data) < _PAGE or (isinstance(total_pages, int) and page >= total_pages):
                break
    return rows


async def _locked(db: AsyncSession) -> bool:
    if db.bind.dialect.name != "postgresql":
        return True
    got = await db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    return bool(got.scalar())


async def sync(db: AsyncSession, now: datetime | None = None) -> int:
    """Ledger rows for API key calls not yet written; returns how many were added. Commits."""
    if not await _locked(db):
        return 0
    keys = (await db.exec(select(ApiKey))).all()
    holders: dict[str, ApiKey] = {}
    for row in keys:
        try:
            holders[key_hash(settings_store.decrypt_secret(row.secret))] = row
        except Exception:  # noqa: BLE001 — an unreadable key is skipped, not fatal
            log.info("api key %s unreadable; usage not synced", row.id)
    if not holders:
        return 0

    now = now or datetime.now(UTC)
    newest = (
        await db.exec(
            select(func.max(CreditLedger.created_at)).where(CreditLedger.reason == API_KEY_REASON)
        )
    ).one()
    newest = _when(newest)
    cycle = credits_service.cycle_start(now)
    start = newest - _OVERLAP if newest else cycle
    end = now - _LAG
    if start >= end:
        return 0

    catalog = (await models_service.list_models()).get("models") or []
    added = 0
    charged: dict[str, int] = defaultdict(int)
    for hashed, key in holders.items():
        rows = await spend_logs(hashed, start, end)
        if not rows:
            continue
        fresh = [
            r
            for r in rows
            if r.get("api_key") == hashed
            and r.get("request_id")
            and r.get("status") in (None, "", "success")
        ]
        refs = [str(r["request_id"])[:120] for r in fresh]
        known = (
            set(
                (
                    await db.exec(
                        select(CreditLedger.external_ref).where(
                            col(CreditLedger.external_ref).in_(refs)
                        )
                    )
                ).all()
            )
            if refs
            else set()
        )
        for row, ref in zip(fresh, refs, strict=True):
            if ref in known:
                continue
            known.add(ref)
            at = _when(row.get("startTime")) or now
            model_id = str(row.get("model_group") or row.get("model") or "")[:200] or None
            tokens_in = int(row.get("prompt_tokens") or 0)
            tokens_out = int(row.get("completion_tokens") or 0)
            model = models_service.find(catalog, model_id) if model_id else None
            credits = credits_service.charge_for_tokens(model or {}, tokens_in, tokens_out)
            db.add(
                CreditLedger(
                    user_id=key.user_id,
                    delta=-credits,
                    reason=API_KEY_REASON,
                    model=model_id,
                    surface=API_SURFACE,
                    units=tokens_in + tokens_out,
                    unit="tokens",
                    external_ref=ref,
                    created_at=at,
                )
            )
            # a call from a past cycle stays in the ledger but not in this month's balance
            if credits and at >= cycle:
                charged[key.user_id] += credits
            added += 1
    for user_id, credits in charged.items():
        await db.exec(
            update(User)
            .where(col(User.id) == user_id)
            .values(credits_used=User.credits_used + credits)
        )
    await db.commit()
    return added


async def watch() -> None:
    """Boot-time loop; failures logged."""
    while True:
        try:
            async with SessionLocal() as db:
                added = await sync(db)
            if added:
                log.info("api key usage: %d calls entered the ledger", added)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed pass is logged, the next one retries
            log.warning("api key usage sync failed: %s", exc)
        await asyncio.sleep(_INTERVAL)


__all__ = ["API_KEY_REASON", "API_SURFACE", "key_hash", "spend_logs", "sync", "watch"]
