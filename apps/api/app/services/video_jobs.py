"""Background understanding of audio/video files, per replica, claimed like OCR jobs.

A queued file is claimed (SKIP LOCKED, a claim token, a lease another replica takes over when
it runs out) and taken through:

1. probe — duration and streams, by the media worker (`media_client`, stock ffmpeg);
2. frames — scene-change times, each frame fetched once, near-duplicates dropped by a
   difference hash, thinned to `video_frames_per_minute` (at most `video_max_frames`), and
   stored with a thumbnail beside the video;
3. audio and vision, side by side — ~10-minute FLAC pieces (5 s overlap) to the internal
   Whisper for timed segments; each kept frame to the strict-local vision model (`video_read`)
   for its text and a one-line description, a repeated picture read once;
4. writing — speech and pictures merged into the time-coded document (`video_doc`), stored as
   the file's text so search, context and digests use it, plus structured segments for
   citations and the player.

Each finished piece or frame is a `MediaPart` row, so a job taken over after a restart resumes
there. Side calls are billed like others: transcription as seconds (self-hosted Whisper is
free), vision at the model's full rate (no cache discount) or a zero-credit usage row.
Nothing is sent outside the deployment whatever the chat model is.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlmodel import col, select

from app.core import db as core_db
from app.core import logs
from app.core.config import settings
from app.models.user import User, utcnow
from app.models.workspace import FileUpload, MediaPart, StoredFile
from app.services import files as file_service
from app.services import media_client, transcribe, video_doc, video_read
from app.services import ocr as ocr_service
from app.services.credits import charge_for_tokens, record_units, settle

log = logging.getLogger(__name__)

#: stale-claim age (dead replica); every finished step renews it
LEASE = timedelta(minutes=10)
RETRY_AFTER = timedelta(seconds=90)
MAX_ATTEMPTS = 3
_IDLE_SEC = 30.0
#: pieced uploads left unfinished this long are removed
UPLOAD_TTL = timedelta(hours=24)

OPEN_STATES = ("queued", "running")
REASON_SPEECH = "media.transcribe"
REASON_VISION = "media.vision"
STT_MODEL = "whisper"

GAVE_UP = "영상을 읽지 못했습니다. 잠시 뒤 다시 올려 주세요."
NO_WORKER = "영상 처리 서버가 연결되지 않아 영상을 읽지 못했습니다."
NOTHING = "영상에서 소리도 화면도 찾지 못했습니다."
UNREADABLE = "영상 파일을 열 수 없습니다. 다른 형식(mp4, webm)으로 바꿔 올려 주세요."
NO_CREDITS = "크레딧이 모자라 영상의 화면을 읽지 못했습니다."


def too_long(minutes: int) -> str:
    return f"영상이 너무 깁니다. {minutes}분 이하로 나눠 올려 주세요."


#: a vision answer budget per frame, for the credit check of a priced vision model
_FRAME_TOKENS_IN = 1600
_FRAME_TOKENS_OUT = 300

_WAKE = asyncio.Event()


def wake() -> None:
    _WAKE.set()


def is_media(mime: str) -> bool:
    return (mime or "").startswith(("audio/", "video/"))


def enabled() -> bool:
    return media_client.configured()


def mark(stored: StoredFile) -> bool:
    """Queue an audio/video file on its row; the caller commits, then `wake()`."""
    if not (enabled() and is_media(stored.mime)):
        return False
    stored.media_state = "queued"
    stored.media_stage = None
    stored.media_done = 0
    stored.media_total = 0
    stored.media_attempts = 0
    stored.media_claimed_at = None
    stored.media_claim = None
    stored.media = None
    stored.error = None
    return True


def estimate(seconds: float, model: dict | None) -> dict:
    """What reading a video this long takes: frames read, minutes of work (measured on the
    self-hosted Whisper and flash, see docs/architecture.md), credits (0 for self-hosted)."""
    seconds = max(0.0, float(seconds or 0))
    frames = video_doc.frame_cap(seconds, settings.video_frames_per_minute,
                                 settings.video_max_frames)
    credits = charge_for_tokens(model or {}, frames * _FRAME_TOKENS_IN,
                                frames * _FRAME_TOKENS_OUT) if model else 0
    # about a tenth of real time for speech, plus a second or two per frame
    minutes = max(1, round((seconds * 0.1 + frames * 1.5) / 60))
    return {
        "seconds": round(seconds),
        "frames": frames,
        "minutes": minutes,
        "credits": credits,
        "maxMinutes": settings.video_max_minutes,
        "allowed": seconds <= settings.video_max_minutes * 60,
    }


def _claimable():
    now = utcnow()
    return or_(
        and_(StoredFile.media_state == "queued",
             or_(col(StoredFile.media_claimed_at).is_(None),
                 StoredFile.media_claimed_at < now - RETRY_AFTER)),
        and_(StoredFile.media_state == "running", StoredFile.media_claimed_at < now - LEASE),
    )


async def claim(db) -> StoredFile | None:
    stored = (await db.exec(
        select(StoredFile).where(_claimable()).order_by(col(StoredFile.created_at)).limit(1)
        .with_for_update(skip_locked=True)
    )).first()
    if stored is None:
        return None
    stored.media_state = "running"
    stored.media_claimed_at = utcnow()
    stored.media_claim = uuid4().hex
    stored.media_attempts = int(stored.media_attempts or 0) + 1
    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    return stored


class LostClaim(RuntimeError):
    """File deleted or job taken over; stop without writing."""


class Final(RuntimeError):
    """The file cannot be read however often it is tried; the message is for people."""


async def _hold(db, file_id: str, token: str) -> StoredFile:
    stored = (await db.exec(
        select(StoredFile).where(StoredFile.id == file_id).with_for_update()
        .execution_options(populate_existing=True)
    )).first()
    if stored is None or stored.media_state != "running" or stored.media_claim != token:
        raise LostClaim(file_id)
    return stored


async def _parts(db, file_id: str) -> dict[tuple[str, int], MediaPart]:
    rows = (await db.exec(select(MediaPart).where(MediaPart.file_id == file_id))).all()
    return {(row.kind, row.idx): row for row in rows}


class _Job:
    """One claimed file's run. Every write is a short session of its own under the claim, so
    the speech and vision steps running side by side never share a connection."""

    def __init__(self, stored: StoredFile, token: str):
        self.file_id = stored.id
        self.key = stored.storage_key
        self.name = stored.name
        self.user_id = stored.user_id
        self.token = token
        self.media: dict = dict(stored.media or {})
        self.lock = asyncio.Lock()
        self.timings: dict[str, float] = dict(self.media.get("timings") or {})

    async def save(self, *, stage: str | None = None, done: int | None = None,
                   total: int | None = None, add: list | None = None,
                   bill=None, step: bool = False) -> None:
        async with self.lock, core_db.SessionLocal() as db:
            stored = await _hold(db, self.file_id, self.token)
            if stage is not None:
                stored.media_stage = stage
            if done is not None:
                stored.media_done = done
            if step:
                stored.media_done = int(stored.media_done or 0) + 1
            if total is not None:
                stored.media_total = total
            self.media["timings"] = self.timings
            stored.media = dict(self.media)
            stored.media_claimed_at = utcnow()
            db.add(stored)
            for row in add or ():
                db.add(row)
            if bill is not None:
                owner = await db.get(User, self.user_id)
                if owner is not None:
                    bill(db, owner)
            await db.commit()


async def _probe(job: _Job) -> None:
    if "duration" in job.media:
        return
    await job.save(stage="probe")
    started = time.monotonic()
    try:
        info = await media_client.probe(job.key)
    except media_client.MediaError as exc:
        if exc.final:
            raise Final(UNREADABLE) from exc
        raise
    job.timings["probe"] = round(time.monotonic() - started, 2)
    if not info.get("hasAudio") and not info.get("hasVideo"):
        raise Final(NOTHING)
    duration = float(info.get("duration") or 0.0)
    if duration > settings.video_max_minutes * 60:
        raise Final(too_long(settings.video_max_minutes))
    job.media.update({
        "duration": round(duration, 2),
        "hasAudio": bool(info.get("hasAudio")),
        "hasVideo": bool(info.get("hasVideo")),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
    })
    await job.save()


async def _frames(job: _Job) -> None:
    """Pick and store the keyframes once (`media["frames"]`); a rerun keeps them."""
    if "frames" in job.media:
        return
    if not job.media.get("hasVideo"):
        job.media["frames"] = []
        await job.save()
        return
    await job.save(stage="frames")
    started = time.monotonic()
    duration = float(job.media["duration"])
    cap = video_doc.frame_cap(duration, settings.video_frames_per_minute,
                              settings.video_max_frames)
    try:
        candidates = await media_client.scenes(job.key, max_frames=cap * 2)
    except media_client.MediaError as exc:
        if exc.final:
            raise Final(UNREADABLE) from exc
        raise
    job.timings["scenes"] = round(time.monotonic() - started, 2)
    gate = asyncio.Semaphore(2)

    async def fetch(t: float) -> bytes:
        async with gate:
            try:
                return await media_client.frame(job.key, t)
            except media_client.MediaError as exc:
                if exc.final:
                    # a damaged spot: that frame is skipped
                    return b""
                raise

    times = [float(c.get("t") or 0.0) for c in candidates]
    pictures = await asyncio.gather(*(fetch(t) for t in times))
    kept: list[tuple[float, int, bytes]] = []
    for t, picture in zip(times, pictures, strict=True):
        if not picture:
            continue
        digest = await asyncio.to_thread(video_read.picture_hash, picture)
        if kept and video_doc.distance(digest, kept[-1][1]) <= video_doc.SAME_PICTURE:
            # the same picture as the last kept one (a cursor moved, the speaker gestured)
            continue
        kept.append((t, digest, picture))
    times = video_doc.thin([t for t, _, _ in kept], cap)
    wanted = set(times)
    kept = [frame for frame in kept if frame[0] in wanted]
    folder = file_service.frames_dir(job.key)
    frames = []
    for n, (t, digest, picture) in enumerate(kept):
        small = await asyncio.to_thread(video_read.thumbnail, picture)
        await asyncio.to_thread(_write, f"{folder}/{n}.jpg", picture)
        await asyncio.to_thread(_write, f"{folder}/{n}s.jpg", small)
        frames.append({"n": n, "t": round(t, 2), "hash": f"{digest:016x}"})
    job.media["frames"] = frames
    job.timings["frames"] = round(time.monotonic() - started, 2)
    await job.save()


def _write(key: str, data: bytes) -> None:
    path = file_service.blob_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def _speech(job: _Job, pieces: list[tuple[float, float]], have: set[int]) -> None:
    started = time.monotonic()
    language: str | None = job.media.get("language") or None
    for idx, (start, length) in enumerate(pieces):
        if idx in have:
            continue
        try:
            audio = await media_client.audio(job.key, start, length)
        except media_client.MediaError as exc:
            if exc.final:
                # a damaged stretch: the rest of the recording still counts
                audio = b""
            else:
                raise
        segments: list[dict] = []
        if audio:
            segments, heard = await transcribe.segments_of(audio, "piece.flac", language)
            if heard and not language:
                # the first piece decides the language for the rest (a long recording keeps one)
                language = heard
                job.media["language"] = heard
        # the overlap with the piece before is heard twice but billed once
        seconds = int(round(length - (video_doc.OVERLAP_SEC if idx else 0.0)))

        def bill(db, owner: User, seconds: int = seconds) -> None:
            record_units(db, owner, reason=REASON_SPEECH, model=STT_MODEL, units=seconds,
                         unit="seconds")

        row = MediaPart(file_id=job.file_id, kind="audio", idx=idx, start=start,
                        data={"segments": segments, "length": length})
        job.timings["audio"] = round(job.timings.get("audio", 0.0)
                                     + (time.monotonic() - started), 2)
        started = time.monotonic()
        await job.save(add=[row], bill=bill, step=True)


async def _vision(job: _Job, frames: list[dict], have: dict[int, dict]) -> None:
    todo = [frame for frame in frames if frame["n"] not in have]
    if not todo:
        return
    started = time.monotonic()
    try:
        entry = await video_read.model()
    except video_read.VisionUnavailable as exc:
        log.info("video %s: frames not read: %s", job.file_id, exc)
        job.media["notes"] = [*job.media.get("notes", []), "vision_unavailable"]
        await job.save()
        return
    async with core_db.SessionLocal() as db:
        owner = await db.get(User, job.user_id)
        key = await ocr_service.api_key_for(owner) if owner is not None else ""
        priced = bool(entry.get("inputCreditCost") or entry.get("creditCost"))
        need = charge_for_tokens(entry, len(todo) * _FRAME_TOKENS_IN,
                                 len(todo) * _FRAME_TOKENS_OUT) if priced else 0
        short = owner is not None and priced and owner.credits_remaining < need
    if short:
        job.media["notes"] = [*job.media.get("notes", []), "no_credits"]
        await job.save()
        return
    gate = asyncio.Semaphore(max(1, settings.video_vision_parallel))
    # a picture shown again (back to an earlier slide) is read once
    readings: dict[str, asyncio.Future] = {}

    async def one(frame: dict) -> None:
        digest = frame["hash"]
        shared = next((f for h, f in readings.items()
                       if video_doc.distance(int(h, 16), int(digest, 16))
                       <= video_doc.SAME_PICTURE), None)
        spent_in = spent_out = 0
        if shared is not None:
            shown = (await shared).shown
        else:
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            readings[digest] = future
            try:
                async with gate:
                    key_of = f"{file_service.frames_dir(job.key)}/{frame['n']}.jpg"
                    picture = await asyncio.to_thread(file_service.read_blob, key_of)
                    reading = await video_read.read(entry, picture, frame["t"], key)
            except BaseException as exc:
                future.set_exception(exc)
                future.exception()  # retrieved: no "never retrieved" warning
                raise
            future.set_result(reading)
            shown = reading.shown
            spent_in, spent_out = reading.input_tokens, reading.output_tokens

        def bill(db, owner: User) -> None:
            if not (spent_in or spent_out):
                return
            credits = charge_for_tokens(entry, spent_in, spent_out)
            if credits:
                settle(db, owner, credits, reason=REASON_VISION, model=entry["id"])
            else:
                record_units(db, owner, reason=REASON_VISION, model=entry["id"],
                             units=spent_in + spent_out, unit="tokens")

        row = MediaPart(file_id=job.file_id, kind="frame", idx=frame["n"], start=frame["t"],
                        data={"kind": shown.kind, "text": shown.text, "about": shown.about})
        await job.save(add=[row], bill=bill, step=True)

    async with asyncio.TaskGroup() as group:
        for frame in todo:
            group.create_task(one(frame))
    job.timings["vision"] = round(job.timings.get("vision", 0.0)
                                  + (time.monotonic() - started), 2)
    job.media["visionModel"] = entry["id"]
    await job.save()


def document(name: str, media: dict, parts: list[MediaPart]) -> tuple[str, list[dict]]:
    """The time-coded text and segments from the finished parts."""
    pieces = sorted((p for p in parts if p.kind == "audio"), key=lambda p: p.idx)
    said = video_doc.merge_said([(p.start, list((p.data or {}).get("segments") or []))
                                 for p in pieces])
    shown = [
        video_doc.Shown(t=p.start, kind=str((p.data or {}).get("kind") or ""),
                        text=str((p.data or {}).get("text") or ""),
                        about=str((p.data or {}).get("about") or ""), frame=p.idx)
        for p in parts if p.kind == "frame"
    ]
    # kept frames nobody read still mark their scene (the player has their thumbnails)
    read = {s.frame for s in shown}
    shown += [video_doc.Shown(t=float(f["t"]), frame=int(f["n"]))
              for f in media.get("frames") or [] if int(f["n"]) not in read]
    duration = float(media.get("duration") or 0.0)
    parts_ = video_doc.sections(duration, shown, said)
    text = video_doc.render(name, duration, parts_, said_any=bool(said),
                            shown_any=any(s.text or s.about for s in shown))
    return text, video_doc.segments(parts_)


async def process(db, stored: StoredFile, token: str) -> None:
    started = time.monotonic()
    if not enabled():
        raise Final(NO_WORKER)
    job = _Job(stored, token)
    await _probe(job)
    await _frames(job)
    duration = float(job.media["duration"])
    pieces = video_doc.audio_pieces(duration) if job.media.get("hasAudio") else []
    frames = list(job.media.get("frames") or [])
    have = await _parts(db, job.file_id)
    heard = {idx for kind, idx in have if kind == "audio"}
    seen = {idx: row.data or {} for (kind, idx), row in have.items() if kind == "frame"}
    done = len(heard & set(range(len(pieces)))) + len(set(seen) & {f["n"] for f in frames})
    await job.save(stage="reading", total=len(pieces) + len(frames), done=done)
    async with asyncio.TaskGroup() as group:
        group.create_task(_speech(job, pieces, heard))
        group.create_task(_vision(job, frames, seen))

    await job.save(stage="writing")
    parts = list((await _parts(db, job.file_id)).values())
    text, segments = document(job.name, job.media, parts)
    if not segments:
        raise Final(NOTHING)
    job.timings["total"] = round(job.timings.get("total", 0.0) + time.monotonic() - started, 2)
    async with job.lock:
        held = await _hold(db, job.file_id, token)
        job.media["segments"] = segments
        job.media["timings"] = job.timings
        held.media = dict(job.media)
        held.text = file_service.storable(text)
        held.tokens = file_service.estimate_tokens(held.text)
        held.media_state = "done"
        held.media_stage = None
        held.media_claim = None
        held.error = None
        db.add(held)
        await db.commit()
        await db.refresh(held)
    await _text_ready(db, held)


async def _text_ready(db, stored: StoredFile) -> None:
    """The document is in: the search index gets it, as after OCR."""
    from app.services import ocr_jobs

    await ocr_jobs._reindex(db, stored)
    try:
        # per-file digests (fix/project-coverage), when that pipeline is present
        from app.services import digest_jobs  # type: ignore[attr-defined]
    except ImportError:
        return
    await digest_jobs.requeue_on_new_text(db, stored.id)


async def _fail(db, file_id: str, token: str, message: str) -> None:
    stored = await _hold(db, file_id, token)
    stored.media_state = "failed"
    stored.media_stage = None
    stored.media_claim = None
    stored.error = message
    db.add(stored)
    await db.commit()


async def run_one() -> bool:
    """Claim and read one file; False if idle."""
    async with core_db.SessionLocal() as db:
        stored = await claim(db)
        if stored is None:
            return False
        file_id, attempts, token = stored.id, int(stored.media_attempts or 0), stored.media_claim
        try:
            await process(db, stored, token)
        except* LostClaim:
            await db.rollback()
        except* Final as group:
            await db.rollback()
            message = str(group.exceptions[0])
            try:
                await _fail(db, file_id, token, message)
            except LostClaim:
                await db.rollback()
        except* Exception as group:
            first = group.exceptions[0]
            log.warning("video %s failed (attempt %d): %s", file_id, attempts, logs.safe(first))
            await db.rollback()
            try:
                if attempts >= MAX_ATTEMPTS:
                    await _fail(db, file_id, token, GAVE_UP)
                else:
                    held = await _hold(db, file_id, token)
                    held.media_state = "queued"
                    held.media_claim = None
                    held.media_claimed_at = utcnow()
                    db.add(held)
                    await db.commit()
            except LostClaim:
                await db.rollback()
        return True


async def sweep_uploads() -> int:
    """Pieced uploads abandoned for a day: rows and partial files removed."""
    cutoff = utcnow() - UPLOAD_TTL
    async with core_db.SessionLocal() as db:
        rows = (await db.exec(select(FileUpload).where(FileUpload.updated_at < cutoff)
                              .limit(100))).all()
        for row in rows:
            if row.storage_key:
                file_service.delete_blob(row.storage_key)
            await db.delete(row)
        if rows:
            await db.commit()
        return len(rows)


async def watch() -> None:
    """Boot loop: read queued files, wait for an upload or the tick."""
    while True:
        try:
            while await run_one():
                pass
            await sweep_uploads()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the next tick tries again
            log.warning("video loop failed: %s", logs.safe(exc))
        try:
            await asyncio.wait_for(_WAKE.wait(), timeout=_IDLE_SEC)
        except TimeoutError:
            pass
        _WAKE.clear()


__all__ = [
    "GAVE_UP",
    "OPEN_STATES",
    "claim",
    "document",
    "enabled",
    "estimate",
    "is_media",
    "mark",
    "process",
    "run_one",
    "wake",
    "watch",
]
