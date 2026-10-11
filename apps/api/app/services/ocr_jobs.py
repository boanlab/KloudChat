"""Background OCR per replica: SKIP LOCKED claims, per-page commits, stale resume."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlmodel import col, select

from app.core import db as core_db
from app.core import logs
from app.models.user import User, utcnow
from app.models.workspace import FilePage, StoredFile
from app.services import files as file_service
from app.services import index_client, ocr
from app.services.credits import record_units

log = logging.getLogger(__name__)

#: stale-claim age (dead replica)
LEASE = timedelta(minutes=10)
#: retry pause
RETRY_AFTER = timedelta(seconds=90)
MAX_ATTEMPTS = 3
#: idle sleep
_IDLE_SEC = 30.0

OPEN_STATES = ("queued", "running")

_WAKE = asyncio.Event()

GAVE_UP = "글자를 읽지 못했습니다. 잠시 뒤 다시 올려 주세요."
TURNED_OFF = "글자 읽기가 꺼져 있어 스캔한 쪽을 읽지 못했습니다."


def wake() -> None:
    """Upload queued: check now."""
    _WAKE.set()


async def queue_if_scanned(stored: StoredFile, data: bytes) -> bool:
    """Mark a new upload for OCR; caller commits then wake(). Never raises."""
    try:
        cfg = await ocr.config()
        if cfg.mode is None:
            return False
        found = await asyncio.to_thread(ocr.plan, stored.name, stored.mime, data, cfg.max_pages)
    except Exception as exc:  # noqa: BLE001 — the upload stands without OCR
        log.info("ocr not planned for %s: %s", stored.id, logs.safe(exc))
        return False
    if found is None or not found.pages:
        return False
    if found.kind == "pdf" and not cfg.engine_url:
        # PDF pages rasterized only via the engine
        return False
    stored.ocr_state = "queued"
    stored.ocr_total = len(found.pages)
    stored.ocr_done = 0
    stored.ocr_review = 0
    stored.ocr_attempts = 0
    stored.ocr_claimed_at = None
    # text layer until pages are read
    stored.text = ocr.joined(sorted(found.layer.items()), found.kind)
    stored.tokens = file_service.estimate_tokens(stored.text)
    stored.error = None
    return True


def _claimable():
    now = utcnow()
    return or_(
        and_(
            StoredFile.ocr_state == "queued",
            or_(
                col(StoredFile.ocr_claimed_at).is_(None),
                StoredFile.ocr_claimed_at < now - RETRY_AFTER,
            ),
        ),
        and_(StoredFile.ocr_state == "running", StoredFile.ocr_claimed_at < now - LEASE),
    )


async def claim(db) -> StoredFile | None:
    """Oldest unheld job, claimed with a fresh ocr_claim."""
    query = (
        select(StoredFile)
        .where(_claimable())
        .order_by(col(StoredFile.created_at))
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    stored = (await db.exec(query)).first()
    if stored is None:
        return None
    stored.ocr_state = "running"
    stored.ocr_claimed_at = utcnow()
    stored.ocr_claim = uuid4().hex
    stored.ocr_attempts = int(stored.ocr_attempts or 0) + 1
    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    return stored


class LostClaim(RuntimeError):
    """File deleted or job taken over; stop without writing."""


async def _hold(db, file_id: str, token: str) -> StoredFile:
    """Job row locked if it still carries this worker's claim; gate for every write."""
    stored = (
        await db.exec(
            select(StoredFile)
            .where(StoredFile.id == file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if stored is None or stored.ocr_state != "running" or stored.ocr_claim != token:
        raise LostClaim(file_id)
    return stored


async def _pages(db, file_id: str) -> dict[int, FilePage]:
    rows = (await db.exec(select(FilePage).where(FilePage.file_id == file_id))).all()
    return {row.page: row for row in rows}


async def _still_there(db, file_id: str) -> bool:
    return (
        await db.exec(select(StoredFile.id).where(StoredFile.id == file_id))
    ).first() is not None


async def _finish(db, stored: StoredFile, kind: str, *, state: str, error: str | None) -> None:
    """Page text joined, job closed; caller holds the claim."""
    pages = sorted((await _pages(db, stored.id)).values(), key=lambda row: row.page)
    text = ocr.joined([(row.page, row.text) for row in pages], kind)
    if kind == "image" and len(ocr.normalised(text)) < ocr.PHOTO_TEXT_MIN_CHARS:
        # photo with a stray word stays a picture
        text = ""
    stored.text = text
    stored.tokens = file_service.estimate_tokens(text)
    stored.ocr_state = state
    stored.ocr_claim = None
    stored.ocr_done = sum(1 for row in pages if row.source != "text")
    stored.ocr_review = sum(1 for row in pages if row.needs_review)
    # failures say why; done says nothing (ocr_done < ocr_total shows skipped pages)
    stored.error = error if state == "failed" else None
    db.add(stored)
    await db.commit()
    await db.refresh(stored)


async def _reindex(db, stored: StoredFile) -> None:
    """Replace the file's index entries; vectors removed again if the file was deleted mid-write."""
    if not stored.text.strip():
        return
    file_id = stored.id
    collection = None
    try:
        from app.routers import workspace

        collection = await workspace.shelf_of(db, stored)
        if collection:
            await workspace.reindex_file(db, stored, collection)
    except Exception as exc:  # noqa: BLE001 — lexical search still finds it
        log.info("ocr reindex of %s skipped: %s", file_id, logs.safe(exc))
        await db.rollback()
    if collection and not await _still_there(db, file_id):
        if not await index_client.forget_document(collection=collection, doc_id=file_id):
            log.warning("ocr: vectors of deleted file %s may remain in its shelf", file_id)


async def process(db, stored: StoredFile, token: str) -> None:
    """Read missing pages of a claimed file, committing each."""
    file_id = stored.id
    cfg = await ocr.config()
    kind = "pdf" if stored.mime == "application/pdf" else "image"
    if cfg.mode is None:
        await _finish(db, await _hold(db, file_id, token), kind, state="failed", error=TURNED_OFF)
        return
    try:
        data = await asyncio.to_thread(file_service.read_blob, stored.storage_key)
    except OSError:
        await _finish(db, await _hold(db, file_id, token), kind, state="failed", error=GAVE_UP)
        return
    plan = await asyncio.to_thread(ocr.plan, stored.name, stored.mime, data, cfg.max_pages)
    if plan is None:
        await _finish(db, await _hold(db, file_id, token), kind, state="done", error=None)
        return
    kind = plan.kind
    stored = await _hold(db, file_id, token)
    have = await _pages(db, file_id)
    for page, text in plan.layer.items():
        if page not in have:
            db.add(FilePage(file_id=file_id, page=page, source="text", text=text))
    # pages past the limit counted, never read
    stored.ocr_total = len(plan.pages) + plan.skipped
    stored.ocr_claimed_at = utcnow()
    db.add(stored)
    await db.commit()
    done = {page for page in have if page in plan.pages}

    owner = await db.get(User, stored.user_id)
    key = await ocr.api_key_for(owner) if owner is not None else ""
    for page in plan.pages:
        if page in done:
            continue
        read = await ocr.read_page(cfg, data, plan.kind, stored.mime, page, key)
        stored = await _hold(db, file_id, token)
        db.add(
            FilePage(
                file_id=file_id,
                page=page,
                source=read.source,
                text=read.text,
                confidence=read.confidence,
                needs_review=read.needs_review,
                has_table=read.has_table,
                layout=read.layout,
            )
        )
        done.add(page)
        stored.ocr_done = len(done)
        stored.ocr_review += 1 if read.needs_review else 0
        stored.ocr_claimed_at = utcnow()
        db.add(stored)
        if owner is not None:
            if read.layout is not None:
                record_units(
                    db, owner, reason="ocr.engine", model="ocr-engine", units=1, unit="pages"
                )
            if read.vision_used:
                record_units(
                    db, owner, reason="ocr.vision", model="ocr-vision", units=1, unit="pages"
                )
        await db.commit()

    stored = await _hold(db, file_id, token)
    await _finish(db, stored, kind, state="done", error=None)
    await _reindex(db, stored)


async def run_one() -> bool:
    """Claim and read one file; False if idle."""
    async with core_db.SessionLocal() as db:
        stored = await claim(db)
        if stored is None:
            return False
        # read first: rollback expires attributes
        file_id, attempts, token = stored.id, int(stored.ocr_attempts or 0), stored.ocr_claim
        kind = "pdf" if stored.mime == "application/pdf" else "image"
        try:
            await process(db, stored, token)
        except asyncio.CancelledError:
            raise
        except LostClaim:
            # deleted or taken over
            await db.rollback()
        except Exception as exc:  # noqa: BLE001 — a reader hiccup is tried again later
            refused = isinstance(exc, ocr.OcrRefused)
            if not refused:
                log.warning("ocr of %s failed (attempt %d): %s", file_id, attempts, logs.safe(exc))
            await db.rollback()
            try:
                stored = await _hold(db, file_id, token)
            except LostClaim:
                await db.rollback()
                return True
            if refused or attempts >= MAX_ATTEMPTS:
                # refused documents won't improve on retry
                await _finish(
                    db, stored, kind, state="failed", error=str(exc) if refused else GAVE_UP
                )
                await _reindex(db, stored)
            else:
                stored.ocr_state = "queued"
                stored.ocr_claim = None
                stored.ocr_claimed_at = utcnow()
                db.add(stored)
                await db.commit()
        return True


async def watch() -> None:
    """Boot loop: read queued files, wait for upload or tick."""
    while True:
        try:
            while await run_one():
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the next tick tries again
            log.warning("ocr loop failed: %s", logs.safe(exc))
        try:
            await asyncio.wait_for(_WAKE.wait(), timeout=_IDLE_SEC)
        except TimeoutError:
            pass
        _WAKE.clear()


__all__ = [
    "GAVE_UP",
    "OPEN_STATES",
    "claim",
    "process",
    "queue_if_scanned",
    "run_one",
    "wake",
    "watch",
]
