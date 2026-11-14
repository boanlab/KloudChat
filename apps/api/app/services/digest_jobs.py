"""Background digests of project files (`file_digest`), per replica, claimed like OCR jobs.

A file is queued when its text arrives (upload, page snapshot, finished OCR), when a turn finds
it without a current digest (lazy backfill), or by the administrator's backfill. A worker claims
the oldest queued file, asks the strict-local digest model, stores the digest and bills the call
as a side call: priced models are charged in full (no cache discount), self-hosted ones leave a
zero-credit usage row with the tokens.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import and_, or_, update
from sqlmodel import col, select

from app.core import db as core_db
from app.core import logs
from app.models.user import User, utcnow
from app.models.workspace import StoredFile
from app.services import file_digest
from app.services import litellm as litellm_service
from app.services.credits import charge_for_tokens, record_units, settle

log = logging.getLogger(__name__)

#: stale-claim age (dead replica)
LEASE = timedelta(minutes=10)
#: pause before a failed file is tried again
RETRY_AFTER = timedelta(seconds=90)
MAX_ATTEMPTS = 3
#: files digested at once per replica (the flash model takes many requests side by side)
WORKERS = 3
_IDLE_SEC = 60.0
REASON = "knowledge.digest"

_WAKE = asyncio.Event()
#: set while `watch` runs in this process: lazy queueing only then
_RUNNING = False
#: queueing tasks in flight, kept so they are not collected mid-run
_PENDING: set[asyncio.Task] = set()


def wake() -> None:
    _WAKE.set()


def needs_digest(stored: StoredFile) -> bool:
    """Project text without a current digest, not already queued or given up on."""
    if not (stored.project_id and (stored.text or "").strip()):
        return False
    if file_digest.stored_digest(stored) is not None:
        return False
    if stored.digest_state in ("queued", "running"):
        return False
    # a failed file is asked again only when its text changed since
    return not (stored.digest_state == "failed"
                and int((stored.digest or {}).get("chars") or -1) == len(stored.text or ""))


def mark(stored: StoredFile) -> bool:
    """Queue `stored` on its row; the caller commits, then `wake()`."""
    if not (stored.project_id and (stored.text or "").strip()):
        return False
    stored.digest_state = "queued"
    stored.digest_attempts = 0
    stored.digest_claimed_at = None
    return True


async def queue(db, file_ids: Iterable[str]) -> int:
    """Queue these files (those still needing a digest); returns how many. Commits."""
    ids = list(dict.fromkeys(file_ids))
    if not ids:
        return 0
    rows = (await db.exec(select(StoredFile).where(col(StoredFile.id).in_(ids)))).all()
    queued = 0
    for stored in rows:
        if needs_digest(stored) and mark(stored):
            db.add(stored)
            queued += 1
    if queued:
        await db.commit()
        wake()
    return queued


def request(file_ids: Iterable[str]) -> None:
    """Lazy backfill from a turn: queue these files in the background, on a session of its
    own, without holding the turn. A no-op where no worker runs (tests, one-off scripts)."""
    ids = list(file_ids)
    if not ids or not _RUNNING:
        return

    async def run() -> None:
        try:
            async with core_db.SessionLocal() as db:
                await queue(db, ids)
        except Exception as exc:  # noqa: BLE001 — the next use asks again
            log.info("digest queueing failed: %s", logs.safe(exc))

    task = asyncio.get_running_loop().create_task(run())
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def backfill(db, *, project_id: str | None = None, force: bool = False) -> dict[str, int]:
    """Queue every project file (of one project, or all) without a current digest; `force`
    queues those with one too. Commits; returns counts."""
    query = select(StoredFile).where(col(StoredFile.project_id).is_not(None))
    if project_id:
        query = query.where(StoredFile.project_id == project_id)
    rows = (await db.exec(query)).all()
    queued = 0
    for stored in rows:
        if (force and (stored.text or "").strip()
                and stored.digest_state not in ("queued", "running")) or needs_digest(stored):
            if mark(stored):
                db.add(stored)
                queued += 1
    if queued:
        await db.commit()
        wake()
    return {"total": len(rows), "queued": queued}


def _claimable():
    now = utcnow()
    return or_(
        and_(
            StoredFile.digest_state == "queued",
            or_(col(StoredFile.digest_claimed_at).is_(None),
                StoredFile.digest_claimed_at < now - RETRY_AFTER),
        ),
        and_(StoredFile.digest_state == "running", StoredFile.digest_claimed_at < now - LEASE),
    )


async def claim(db) -> StoredFile | None:
    stored = (await db.exec(
        select(StoredFile).where(_claimable()).order_by(col(StoredFile.created_at)).limit(1)
        .with_for_update(skip_locked=True)
    )).first()
    if stored is None:
        return None
    stored.digest_state = "running"
    stored.digest_claimed_at = utcnow()
    stored.digest_attempts = int(stored.digest_attempts or 0) + 1
    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    return stored


async def _complete(model: str, prompt: str, api_key: str) -> tuple[str, dict]:
    # late import: `report` is heavy and imports the pipeline
    from app.services import report

    return await report._complete(model, [{"role": "user", "content": prompt}], api_key,
                                  file_digest._MAX_TOKENS)


async def _still_ours(db, file_id: str, claimed_at) -> StoredFile | None:
    stored = (await db.exec(
        select(StoredFile).where(StoredFile.id == file_id)
        .execution_options(populate_existing=True)
    )).first()
    if stored is None or stored.digest_state != "running" or stored.digest_claimed_at != claimed_at:
        return None
    return stored


async def process(db, stored: StoredFile) -> None:
    """Digest one claimed file and bill the call; raises on a model failure."""
    file_id, claimed_at = stored.id, stored.digest_claimed_at
    name, text = stored.name, stored.text or ""
    owner = await db.get(User, stored.user_id)
    model = await file_digest.model_for() if text.strip() else None
    if model is None or owner is None:
        # no strict-local model served: the code digest stands; nothing is sent anywhere
        if (held := await _still_ours(db, file_id, claimed_at)) is not None:
            held.digest_state = "done"
            held.digest = None
            db.add(held)
            await db.commit()
        return
    api_key = litellm_service.user_key(owner) or ""
    if not api_key:
        from app.services import settings_store

        api_key = (await settings_store.litellm_config())[1]
    answer, usage = await _complete(model["id"], file_digest.prompt(name, text), api_key)
    made = file_digest.parse(answer, name, text)
    held = await _still_ours(db, file_id, claimed_at)
    if held is None:
        await db.rollback()
        return
    owner = await db.get(User, held.user_id)
    spent_in = int((usage or {}).get("inputTokens") or 0)
    spent_out = int((usage or {}).get("outputTokens") or 0)
    if owner is not None:
        # a side call: billed at the full rate, cached input included
        credits = charge_for_tokens(model, spent_in, spent_out)
        if credits:
            settle(db, owner, credits, reason=REASON, model=model["id"])
        else:
            record_units(db, owner, reason=REASON, model=model["id"],
                         units=spent_in + spent_out, unit="tokens")
    if made is None:
        raise ValueError("digest answer unreadable")
    if (held.text or "") != text:
        # the text changed under the call (OCR finished): asked again on the new text
        held.digest_state = "queued"
        held.digest_claimed_at = None
        held.digest_attempts = 0
    else:
        held.digest = made.wire()
        held.digest_model = model["id"]
        held.digested_at = utcnow()
        held.digest_state = "done"
    db.add(held)
    await db.commit()


async def run_one() -> bool:
    """Claim and digest one file; False when idle."""
    async with core_db.SessionLocal() as db:
        stored = await claim(db)
        if stored is None:
            return False
        file_id, attempts, claimed_at = stored.id, stored.digest_attempts, stored.digest_claimed_at
        try:
            await process(db, stored)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — tried again later, then left to the code digest
            log.warning("digest of %s failed (attempt %d): %s", file_id, attempts, logs.safe(exc))
            await db.rollback()
            held = await _still_ours(db, file_id, claimed_at)
            if held is not None:
                if attempts >= MAX_ATTEMPTS:
                    held.digest_state = "failed"
                    # the text it failed on: asked again only when that changes
                    held.digest = {"chars": len(held.text or "")}
                else:
                    held.digest_state = "queued"
                    held.digest_claimed_at = utcnow()
                db.add(held)
                await db.commit()
        return True


async def _worker() -> None:
    while True:
        try:
            while await run_one():
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the next tick tries again
            log.warning("digest loop failed: %s", logs.safe(exc))
        try:
            await asyncio.wait_for(_WAKE.wait(), timeout=_IDLE_SEC)
        except TimeoutError:
            pass
        _WAKE.clear()


async def watch() -> None:
    """Boot loop: `WORKERS` claimers side by side."""
    global _RUNNING
    _RUNNING = True
    try:
        await asyncio.gather(*(_worker() for _ in range(WORKERS)))
    finally:
        _RUNNING = False


async def requeue_on_new_text(db, file_id: str) -> None:
    """A file's text was replaced (OCR): its digest is made again. Commits."""
    await db.exec(
        update(StoredFile)
        .where(StoredFile.id == file_id, col(StoredFile.project_id).is_not(None))
        .values(digest_state="queued", digest_attempts=0, digest_claimed_at=None)
    )
    await db.commit()
    wake()


__all__ = ["MAX_ATTEMPTS", "REASON", "backfill", "claim", "mark", "needs_digest", "process",
           "queue", "request", "requeue_on_new_text", "run_one", "wake", "watch"]
