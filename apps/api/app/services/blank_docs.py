"""A blank document nobody touched is dropped when its screen is left.

「빈 문서로 시작」 makes a conversation with an empty office file open beside it. If the person
leaves without doing anything, the file and its empty conversation go; one edit, one message, one
share or one rename and they stay. The route marks a blank one on creation (`Artifact.data`
keeps the title it was made with); a save rebuilds `data`, so the mark goes with any edit.

Only a screen left inside the app asks for the drop. A reload, a closed tab or a phone sent to the
background cannot be told from each other in the browser, so none asks: the sweep takes care of
what is left. A tab that has the document open pings every minute (`ping`); another tab's recent
ping keeps it from the drop, and from the sweep.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, timedelta

from sqlalchemy import Text, cast, func
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.chat import ChatSession, Message
from app.models.user import utcnow
from app.models.workspace import (
    Artifact,
    ArtifactVersion,
    BlankDocSeen,
    Job,
    Share,
    StoredFile,
)
from app.services import index_cleanup, office_doc
from app.services.office_doc import BLANK_KEY

log = logging.getLogger(__name__)

#: a save or rename moves `updated_at` by more than this past `created_at`
_SLACK_SEC = 2
_INTERVAL = 30 * 60
_BATCH = 200
#: pages of `_BATCH` one sweep reads at most
_MAX_PAGES = 50
#: a tab pinged this recently still has the document open (the client pings every minute)
PING_FRESH_SEC = 180
#: pings older than this are cleared by the sweep
_PING_KEEP = timedelta(days=1)


async def ping(db: AsyncSession, artifact: Artifact, tab: str) -> None:
    """A tab has this blank document open now. Not an edit: only `blank_doc_seen` changes."""
    if (artifact.data or {}).get(BLANK_KEY) is None or artifact.version != 1:
        return
    row = await db.get(BlankDocSeen, (artifact.id, tab))
    if row is None:
        db.add(BlankDocSeen(artifact_id=artifact.id, tab=tab))
    else:
        row.seen_at = utcnow()
        db.add(row)
    await db.commit()


async def is_pristine(db: AsyncSession, artifact: Artifact, *, tab: str | None = None) -> bool:
    """Blank, still version 1 under the title it was made with, alone in a conversation that
    has no message, file, share or job, and open in no other tab. Any doubt is a no."""
    if artifact.kind not in office_doc.FILE_KINDS or artifact.version != 1:
        return False
    made = (artifact.data or {}).get(BLANK_KEY)
    if not isinstance(made, str) or made != artifact.title:
        return False
    if (artifact.updated_at - artifact.created_at) > timedelta(seconds=_SLACK_SEC):
        return False
    if not artifact.session_id:
        return False
    session = await db.get(ChatSession, artifact.session_id)
    if session is None or session.user_id != artifact.user_id:
        return False
    if (
        session.title_set_by_user or session.pinned or session.publication_id
        or session.pending or session.summary or session.pipeline_id
        or session.project_id != artifact.project_id
        or session.artifact_id not in (None, artifact.id)
    ):
        return False

    async def count(model, *where) -> int:
        return (await db.exec(select(func.count()).select_from(model).where(*where))).one()

    sid = session.id
    if await count(Message, col(Message.session_id) == sid):
        return False
    if await count(Artifact, col(Artifact.session_id) == sid, col(Artifact.id) != artifact.id):
        return False
    if await count(StoredFile, col(StoredFile.session_id) == sid):
        return False
    if await count(Job, col(Job.session_id) == sid):
        return False
    if await count(Share, (col(Share.artifact_id) == artifact.id) | (col(Share.session_id) == sid)):
        return False
    if await count(ArtifactVersion, col(ArtifactVersion.artifact_id) == artifact.id):
        return False
    fresh = utcnow() - timedelta(seconds=PING_FRESH_SEC)
    others = [col(BlankDocSeen.artifact_id) == artifact.id, col(BlankDocSeen.seen_at) > fresh]
    if tab:
        others.append(col(BlankDocSeen.tab) != tab)
    return not await count(BlankDocSeen, *others)


async def discard(
    db: AsyncSession, artifact: Artifact, *, tab: str | None = None, via: str = "route"
) -> bool:
    """Deletes a pristine document and its empty conversation; False when it is not one.
    `tab` is the one leaving: its own pings do not count. The row is locked while it is judged
    and deleted, so a save arriving at the same moment waits and then finds it gone (or was
    first, and the document stays). No audit row: nothing the person made is lost."""
    artifact_id = artifact.id
    locked = await db.get(Artifact, artifact_id, with_for_update=True, populate_existing=True)
    if locked is None or not await is_pristine(db, locked, tab=tab):
        await db.rollback()
        return False
    session = await db.get(ChatSession, locked.session_id)
    blobs = await office_doc.kept_files(db, [locked])
    index_key = session.index_key if session else None
    made_at = locked.created_at
    if made_at.tzinfo is None:  # a database that keeps no zone (the tests' SQLite)
        made_at = made_at.replace(tzinfo=UTC)
    age = (utcnow() - made_at).total_seconds()
    log.info(
        "blank document %s (%s, %.0fs old) dropped via %s with conversation %s",
        artifact_id, locked.kind.value, age, via, locked.session_id,
    )
    await db.exec(delete(BlankDocSeen).where(col(BlankDocSeen.artifact_id) == artifact_id))
    await db.delete(locked)
    if session is not None:
        await db.delete(session)
    await db.commit()
    office_doc.drop_files(blobs)
    if index_key:
        await index_cleanup.forget_collection(db, collection=index_key, commit=True)
    return True


async def sweep(db: AsyncSession, *, older_than_hours: float) -> int:
    """Pristine blank documents older than this, outside any project, open in no tab.

    Candidates are read in pages by (created_at, id), so a long run of other version-1 documents
    never keeps the later ones from being looked at; a coarse text match on the mark leaves out
    what was never blank. Each document is judged and dropped on its own: one that moved
    underneath (another replica, a save) is skipped and the rest go on."""
    now = utcnow()
    cutoff = now - timedelta(hours=older_than_hours)
    await db.exec(delete(BlankDocSeen).where(col(BlankDocSeen.seen_at) < now - _PING_KEEP))
    await db.commit()
    gone: list[str] = []
    after: tuple | None = None
    for _ in range(_MAX_PAGES):
        query = (
            select(col(Artifact.id), col(Artifact.created_at))
            .where(
                col(Artifact.kind).in_(office_doc.FILE_KINDS),
                col(Artifact.version) == 1,
                col(Artifact.created_at) < cutoff,
                col(Artifact.session_id).is_not(None),
                col(Artifact.project_id).is_(None),
                cast(col(Artifact.data), Text).like(f'%"{BLANK_KEY}"%'),
            )
            .order_by(col(Artifact.created_at), col(Artifact.id))
            .limit(_BATCH)
        )
        if after:
            at, last = after
            query = query.where(
                (col(Artifact.created_at) > at)
                | ((col(Artifact.created_at) == at) & (col(Artifact.id) > last))
            )
        page = [(row[0], row[1]) for row in (await db.exec(query)).all()]
        for artifact_id, _created in page:
            try:
                artifact = await db.get(Artifact, artifact_id)
                if artifact is not None and await discard(db, artifact, via="sweep"):
                    gone.append(artifact_id)
            except Exception as exc:  # noqa: BLE001 - another replica got there first, or a row moved
                await db.rollback()
                log.info("blank document %s kept: %s", artifact_id, exc)
        if len(page) < _BATCH:
            break
        after = (page[-1][1], page[-1][0])
    if gone:
        log.info("blank sweep dropped %d documents: %s", len(gone), ", ".join(gone[:50]))
    return len(gone)


async def watch() -> None:
    """Boot-time loop; failures logged."""
    while True:
        try:
            if settings.blank_doc_sweep_hours > 0:
                async with SessionLocal() as db:
                    await sweep(db, older_than_hours=settings.blank_doc_sweep_hours)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a sweep that fails is logged, not fatal
            log.warning("blank document sweep failed: %s", exc)
        await asyncio.sleep(_INTERVAL)


__all__ = ["BLANK_KEY", "PING_FRESH_SEC", "discard", "is_pristine", "ping", "sweep", "watch"]
