"""Index deletes that must land: a failed one is kept in `index_deletions` and retried."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import db as core_db
from app.core import logs
from app.models.user import utcnow
from app.models.workspace import IndexDeletion, StoredFile
from app.services import index_client

log = logging.getLogger(__name__)

#: boot loop interval
_INTERVAL = 10 * 60
#: rows tried per pass; each may wait on the index's timeout
_BATCH = 50
#: an entry the index refuses this long is given up, with a warning
GIVE_UP = timedelta(days=30)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _remember(db: AsyncSession, collection: str, doc_id: str | None) -> None:
    """Pending row for a delete the index did not take; the caller commits."""
    # no index configured: nothing to delete from; a malformed id never will be
    if not collection or len(collection) > 64 or not await index_client.available():
        return
    if doc_id is not None and not index_client._ID.fullmatch(doc_id):
        return
    if doc_id is None and not index_client._KEY.fullmatch(collection):
        return
    held = (await db.exec(select(IndexDeletion.id).where(
        IndexDeletion.collection == collection,
        col(IndexDeletion.doc_id).is_(None) if doc_id is None
        else IndexDeletion.doc_id == doc_id,
    ))).first()
    if held is None:
        db.add(IndexDeletion(collection=collection, doc_id=doc_id))


async def forget_document(db: AsyncSession, *, collection: str, doc_id: str,
                          commit: bool = False) -> bool:
    """`index_client.forget_document`, a failure kept for retry; `commit` for callers past
    their own commit."""
    if await index_client.forget_document(collection=collection, doc_id=doc_id):
        return True
    await _remember(db, collection, doc_id)
    if commit:
        await db.commit()
    return False


async def forget_collection(db: AsyncSession, *, collection: str, commit: bool = False) -> bool:
    """`index_client.forget_collection`, a failure kept for retry."""
    if await index_client.forget_collection(collection=collection):
        return True
    await _remember(db, collection, None)
    if commit:
        await db.commit()
    return False


async def retry(db: AsyncSession, now: datetime | None = None) -> int:
    """One pass over pending deletes, oldest try first; returns how many landed. Commits."""
    if not await index_client.available():
        return 0
    now = now or utcnow()
    rows = (await db.exec(
        select(IndexDeletion).order_by(col(IndexDeletion.tried_at)).limit(_BATCH)
        .with_for_update(skip_locked=True)
    )).all()
    landed = 0
    for row in rows:
        if row.doc_id is None:
            ok = await index_client.forget_collection(collection=row.collection)
        else:
            live = await db.get(StoredFile, row.doc_id)
            # indexed there again since: that entry is current, not stale
            ok = (live is not None and live.indexed_at is not None
                  and live.index_collection == row.collection) or \
                await index_client.forget_document(collection=row.collection, doc_id=row.doc_id)
        if ok:
            await db.delete(row)
            landed += 1
        elif now - _aware(row.created_at) > GIVE_UP:
            log.warning("index entry %s/%s given up after %d tries",
                        logs.safe(row.collection[:6]), logs.safe(row.doc_id or "*"),
                        row.attempts)
            await db.delete(row)
        else:
            row.attempts += 1
            row.tried_at = now
            db.add(row)
    await db.commit()
    return landed


async def watch() -> None:
    """Boot-time loop; failures logged."""
    while True:
        try:
            async with core_db.SessionLocal() as db:
                await retry(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the next pass retries
            log.warning("index delete retry failed: %s", logs.safe(exc))
        await asyncio.sleep(_INTERVAL)


__all__ = ["GIVE_UP", "forget_collection", "forget_document", "retry", "watch"]
