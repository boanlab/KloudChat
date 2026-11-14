"""Large audio/video uploads in pieces, and what a video job made of them.

Uploads (`/uploads`): a video up to MAX_VIDEO_UPLOAD_MB arrives as pieces of UPLOAD_PIECE_MB,
each appended to a partial file under the storage root, so neither the API (two replicas) nor
nginx (body limit stays at MAX_UPLOAD_MB) ever holds the whole video; a dropped connection
resumes from `received`. `finish` moves the partial file into place and queues the media job
(`video_jobs`); the tab can close from then on.

Media (`/files/{id}/media`): the job's state and the time-coded segments, with signed URLs
for the player — the original with HTTP Range (seeking), and the keyframe thumbnails — that a
`<video>`/`<img>` can load without an Authorization header.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import Field
from sqlmodel import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.models.chat import ChatSession
from app.models.user import utcnow
from app.models.workspace import FileUpload, Project, StoredFile
from app.schemas.auth import Wire
from app.schemas.workspace import FileOut
from app.services import files as file_service
from app.services import media_client, transcribe, video_jobs, video_read

log = logging.getLogger(__name__)
router = APIRouter(tags=["media"])

#: signed media URLs stay valid this long (a lecture watched in one sitting)
URL_TTL_SEC = 6 * 3600
_MB = 1024 * 1024


class UploadStart(Wire):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1)
    type: str = Field(default="", max_length=120)
    project_id: str | None = None
    session_id: str | None = None
    #: the browser's reading of the video's length, when it could; checked before any upload
    seconds: float | None = Field(default=None, ge=0)


class UploadOut(Wire):
    id: str
    size: int
    received: int
    piece_bytes: int


class MediaOut(Wire):
    name: str = ""
    state: str | None = None
    stage: str | None = None
    done: int = 0
    total: int = 0
    error: str | None = None
    duration: float = 0.0
    width: int = 0
    height: int = 0
    has_video: bool = False
    has_audio: bool = False
    language: str = ""
    segments: list[dict] = Field(default_factory=list)
    frames: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    #: the original, signed, seekable (`#t=` works)
    stream_url: str = ""
    #: a keyframe thumbnail: replace `{n}` with the frame number (`{n}s` small)
    frame_url: str = ""
    #: the picture at any moment (the player's poster): replace `{t}` with seconds
    poster_url: str = ""


def _limit_mb() -> int:
    return max(settings.max_video_upload_mb, settings.max_upload_mb)


def _piece_bytes() -> int:
    # under nginx's body limit (MAX_UPLOAD_MB) whatever the settings say
    return max(1, min(settings.upload_piece_mb, settings.max_upload_mb)) * _MB


def _is_media_claim(name: str, declared: str) -> bool:
    declared = (declared or "").split(";")[0].strip().lower()
    if declared.startswith(("audio/", "video/")):
        return True
    return Path(name).suffix.lower() in {
        ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mp3", ".m4a", ".wav", ".ogg",
        ".oga", ".flac", ".aac", ".opus", ".mpeg", ".mpg",
    }


async def _owned_upload(db, user, upload_id: str, *, lock: bool = False) -> FileUpload:
    query = select(FileUpload).where(FileUpload.id == upload_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = (await db.exec(query)).first()
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


@router.get("/media/estimate")
async def media_estimate(user: CurrentUser, seconds: float = Query(0, ge=0, le=86_400)):
    """What reading a video of this length takes, before it is uploaded."""
    try:
        entry = await video_read.model()
    except Exception:  # noqa: BLE001 — no vision model: the estimate is speech only
        entry = None
    out = video_jobs.estimate(seconds, entry)
    out["enabled"] = video_jobs.enabled()
    out["creditsLeft"] = user.credits_remaining
    return out


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def start_upload(payload: UploadStart, user: CurrentUser, db: DbSession):
    name = file_service.safe_name(payload.name)
    if not _is_media_claim(name, payload.type):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="not_media")
    if payload.size > _limit_mb() * _MB:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"file_too_large_{_limit_mb()}mb")
    if payload.seconds and payload.seconds > settings.video_max_minutes * 60:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"video_too_long_{settings.video_max_minutes}min")
    if payload.project_id:
        project = await db.get(Project, payload.project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if payload.session_id:
        session = await db.get(ChatSession, payload.session_id)
        if session is None or session.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    root = file_service.storage_root()
    if shutil.disk_usage(root).free < payload.size * 2 + 512 * _MB:
        raise HTTPException(status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                            detail="storage_full")
    row = FileUpload(user_id=user.id, name=name, size=payload.size,
                     mime=(payload.type or "")[:120], project_id=payload.project_id,
                     session_id=payload.session_id)
    row.storage_key = f"{user.id}/.uploads/{row.id}.part"
    path = file_service.blob_path(row.storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    db.add(row)
    await db.commit()
    return UploadOut(id=row.id, size=row.size, received=0, piece_bytes=_piece_bytes())


@router.get("/uploads/{upload_id}", response_model=UploadOut)
async def upload_state(upload_id: str, user: CurrentUser, db: DbSession):
    """Where to resume: bytes received so far."""
    row = await _owned_upload(db, user, upload_id)
    return UploadOut(id=row.id, size=row.size, received=row.received,
                     piece_bytes=_piece_bytes())


def _append(path: Path, offset: int, data: bytes) -> None:
    with open(path, "r+b") as handle:
        handle.seek(offset)
        handle.write(data)
        handle.truncate(offset + len(data))


@router.put("/uploads/{upload_id}", response_model=UploadOut)
async def put_piece(upload_id: str, request: Request, user: CurrentUser, db: DbSession,
                    offset: int = Query(..., ge=0)):
    """One piece at `offset`; a piece already received (a retry) is acknowledged as is."""
    limit = _piece_bytes()
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                detail="piece_too_large")
    row = await _owned_upload(db, user, upload_id, lock=True)
    end = offset + len(data)
    if end > row.size:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="past_the_end")
    if offset > row.received:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"expected_offset_{row.received}")
    if end > row.received:
        try:
            await asyncio.to_thread(_append, file_service.blob_path(row.storage_key), offset,
                                    bytes(data))
        except OSError as exc:
            log.warning("upload %s piece not written: %s", row.id, exc)
            raise HTTPException(status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                                detail="storage_full") from None
        row.received = end
    row.updated_at = utcnow()
    db.add(row)
    await db.commit()
    return UploadOut(id=row.id, size=row.size, received=row.received, piece_bytes=limit)


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_upload(upload_id: str, user: CurrentUser, db: DbSession):
    row = await _owned_upload(db, user, upload_id, lock=True)
    file_service.delete_blob(row.storage_key)
    await db.delete(row)
    await db.commit()


def _head(path: Path, size: int = 64 * 1024) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(size)


@router.post("/uploads/{upload_id}/finish", response_model=FileOut,
             status_code=status.HTTP_201_CREATED)
async def finish_upload(upload_id: str, user: CurrentUser, db: DbSession):
    """The whole file is in: it becomes a stored file and its media job is queued."""
    row = await _owned_upload(db, user, upload_id, lock=True)
    if row.received != row.size:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"expected_offset_{row.received}")
    partial = file_service.blob_path(row.storage_key)
    head = await asyncio.to_thread(_head, partial)
    mime = file_service.detected_mime(row.name, row.mime, head)
    if not video_jobs.is_media(mime):
        file_service.delete_blob(row.storage_key)
        await db.delete(row)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="not_media")
    stored = StoredFile(user_id=user.id, project_id=row.project_id, session_id=row.session_id,
                        name=row.name, size=row.size, mime=mime)
    stored.storage_key = f"{user.id}/{stored.id}_{file_service.safe_name(row.name)}"
    await asyncio.to_thread(os.replace, partial, file_service.blob_path(stored.storage_key))
    queued = video_jobs.mark(stored)
    if not queued:
        # no media worker: the old way, whole-file transcription while it fits Whisper
        if stored.size <= transcribe.MAX_BYTES:
            try:
                data = await asyncio.to_thread(file_service.read_blob, stored.storage_key)
                stored.text = await file_service.text_of(stored.name, stored.mime, data)
                stored.tokens = file_service.estimate_tokens(stored.text)
            except Exception as exc:  # noqa: BLE001 — the upload stands without text
                stored.error = str(exc)
        else:
            stored.error = video_jobs.NO_WORKER
    db.add(stored)
    await db.delete(row)
    await db.commit()
    await db.refresh(stored)
    if queued:
        video_jobs.wake()
    return FileOut.of(stored)


# ---- the job's results --------------------------------------------------------------------


def _signature(file_id: str, owner: str, expires: int) -> str:
    message = f"media:{file_id}:{owner}:{expires}".encode()
    return hmac.new(settings.jwt_secret.encode(), message, hashlib.sha256).hexdigest()[:32]


def signed_query(stored: StoredFile, now: float | None = None) -> str:
    expires = int((now or time.time()) + URL_TTL_SEC)
    return f"e={expires}&s={_signature(stored.id, stored.user_id, expires)}"


async def _signed_file(db, file_id: str, e: int, s: str) -> StoredFile:
    stored = await db.get(StoredFile, file_id)
    if stored is None or e < time.time() or not hmac.compare_digest(
            s, _signature(file_id, stored.user_id, e)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return stored


@router.get("/files/{file_id}/media", response_model=MediaOut)
async def file_media(file_id: str, user: CurrentUser, db: DbSession):
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    media = dict(stored.media or {})
    query = signed_query(stored)
    base = f"{settings.api_prefix}/files/{stored.id}"
    return MediaOut(
        name=stored.name,
        state=stored.media_state,
        stage=stored.media_stage,
        done=stored.media_done,
        total=stored.media_total,
        error=stored.error if stored.media_state == "failed" else None,
        duration=float(media.get("duration") or 0.0),
        width=int(media.get("width") or 0),
        height=int(media.get("height") or 0),
        has_video=bool(media.get("hasVideo")),
        has_audio=bool(media.get("hasAudio")),
        language=str(media.get("language") or ""),
        segments=list(media.get("segments") or []),
        frames=[{"n": f.get("n"), "t": f.get("t")} for f in media.get("frames") or []],
        notes=[str(n) for n in media.get("notes") or []],
        timings={k: float(v) for k, v in (media.get("timings") or {}).items()},
        stream_url=f"{base}/stream?{query}" if video_jobs.is_media(stored.mime) else "",
        frame_url=f"{base}/frames/{{n}}.jpg?{query}" if media.get("frames") else "",
        poster_url=(f"{base}/poster?at={{t}}&{query}"
                    if media.get("hasVideo") and media_client.configured() else ""),
    )


@router.post("/files/{file_id}/media/retry", response_model=FileOut)
async def retry_media(file_id: str, user: CurrentUser, db: DbSession):
    """A failed video read again from where it stopped (finished pieces are kept)."""
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if stored.media_state != "failed" or not video_jobs.enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_failed")
    stored.media_state = "queued"
    stored.media_attempts = 0
    stored.media_claimed_at = None
    stored.media_claim = None
    stored.error = None
    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    video_jobs.wake()
    return FileOut.of(stored)


def _media_headers(name: str) -> dict[str, str]:
    headers = file_service.download_headers("video/mp4", True, name)
    # signed for hours; the browser may keep what it fetched for the player
    headers["Cache-Control"] = "private, max-age=3600"
    return headers


@router.get("/files/{file_id}/stream")
async def stream_file(file_id: str, db: DbSession, e: int = Query(...),
                      s: str = Query(..., max_length=64)):
    """The original audio/video, with HTTP Range so the player seeks without downloading."""
    stored = await _signed_file(db, file_id, e, s)
    if not video_jobs.is_media(stored.mime):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    try:
        path = file_service.blob_path(stored.storage_key)
        head = await asyncio.to_thread(_head, path, 4096)
    except (OSError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    media, _ = file_service.served_as(stored.name, stored.mime, head)
    if not media.startswith(("audio/", "video/")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return FileResponse(path, media_type=media, headers=_media_headers(stored.name))


@router.get("/files/{file_id}/frames/{frame}")
async def frame_image(file_id: str, frame: str, db: DbSession, e: int = Query(...),
                      s: str = Query(..., max_length=64)):
    """A keyframe (`3.jpg`) or its thumbnail (`3s.jpg`)."""
    stored = await _signed_file(db, file_id, e, s)
    stem = frame.removesuffix(".jpg")
    number = stem.removesuffix("s")
    if not number.isdigit() or len(number) > 4:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    small = "s" if stem.endswith("s") else ""
    key = f"{file_service.frames_dir(stored.storage_key)}/{int(number)}{small}.jpg"
    try:
        path = file_service.blob_path(key)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found") from None
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"X-Content-Type-Options": "nosniff",
                                 "Cache-Control": "private, max-age=3600"})


@router.get("/files/{file_id}/poster")
async def poster(file_id: str, db: DbSession, at: float = Query(0, ge=0, le=86_400),
                 e: int = Query(...), s: str = Query(..., max_length=64)):
    """The picture at `at` seconds, by the media worker (a seek and one frame, under a
    second): the player shows the very moment it opens at, not the nearest keyframe."""
    stored = await _signed_file(db, file_id, e, s)
    if not video_jobs.is_media(stored.mime) or not media_client.configured():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    try:
        picture = await media_client.frame(stored.storage_key, round(at, 1), width=960)
    except media_client.MediaError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_picture") from None
    if not picture:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_picture")
    return Response(content=picture, media_type="image/jpeg",
                    headers={"X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, max-age=3600"})
