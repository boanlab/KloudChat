"""A blank document nobody touched is dropped when its screen is left.

「빈 문서로 시작」 makes a conversation with an empty office file open beside it. If the person
leaves without doing anything, the file and its empty conversation go; one edit, one message, one
share or one rename and they stay. The route marks a blank one on creation (`Artifact.data`
keeps the title it was made with); a save rebuilds `data`, so the mark goes with any edit.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.chat import ChatSession, Message
from app.models.user import utcnow
from app.models.workspace import Artifact, ArtifactVersion, Job, Share, StoredFile
from app.services import index_cleanup, office_doc

log = logging.getLogger(__name__)

#: `Artifact.data` key: the title a blank document was made with
BLANK_KEY = "blank"

#: a save or rename moves `updated_at` by more than this past `created_at`
_SLACK_SEC = 2
_INTERVAL = 30 * 60
_BATCH = 200


def marked(data: dict, title: str) -> dict:
    """`data` of a document just made blank."""
    return {**data, BLANK_KEY: title}


async def is_pristine(db: AsyncSession, artifact: Artifact) -> bool:
    """Blank, still version 1 under the title it was made with, alone in a conversation that
    has no message, file, share or job. Any doubt is a no."""
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
    return not await count(ArtifactVersion, col(ArtifactVersion.artifact_id) == artifact.id)


async def discard(db: AsyncSession, artifact: Artifact) -> bool:
    """Deletes a pristine document and its empty conversation; False when it is not one.
    No audit row: nothing the person made is lost."""
    if not await is_pristine(db, artifact):
        return False
    session = await db.get(ChatSession, artifact.session_id)
    blobs = await office_doc.kept_files(db, [artifact])
    index_key = session.index_key if session else None
    await db.delete(artifact)
    if session is not None:
        await db.delete(session)
    await db.commit()
    office_doc.drop_files(blobs)
    if index_key:
        await index_cleanup.forget_collection(db, collection=index_key, commit=True)
    return True


async def sweep(db: AsyncSession, *, older_than_hours: float) -> int:
    """Pristine blank documents older than this, outside any project, in no one's way."""
    cutoff = utcnow() - timedelta(hours=older_than_hours)
    rows = (
        await db.exec(
            select(Artifact)
            .where(
                col(Artifact.kind).in_(office_doc.FILE_KINDS),
                col(Artifact.version) == 1,
                col(Artifact.created_at) < cutoff,
                col(Artifact.session_id).is_not(None),
                col(Artifact.project_id).is_(None),
            )
            .order_by(col(Artifact.created_at))
            .limit(_BATCH)
        )
    ).all()
    gone = 0
    for artifact in rows:
        try:
            if await discard(db, artifact):
                gone += 1
        except Exception as exc:  # noqa: BLE001 - another replica got there first, or a row moved
            await db.rollback()
            log.info("blank document %s kept: %s", artifact.id, exc)
    return gone


async def watch() -> None:
    """Boot-time loop; failures logged."""
    while True:
        try:
            if settings.blank_doc_sweep_hours > 0:
                async with SessionLocal() as db:
                    gone = await sweep(db, older_than_hours=settings.blank_doc_sweep_hours)
                if gone:
                    log.info("dropped %d untouched blank documents", gone)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a sweep that fails is logged, not fatal
            log.warning("blank document sweep failed: %s", exc)
        await asyncio.sleep(_INTERVAL)


__all__ = ["BLANK_KEY", "discard", "is_pristine", "marked", "sweep", "watch"]
