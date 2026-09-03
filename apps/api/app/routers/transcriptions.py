"""The composer's microphone: a recording in, its words out.

`services/transcribe` has been able to do this since the Whisper shim was
wired up, and nothing called it — the sign-in payload said `dictationEnabled`
and no screen had a button. One endpoint, one recording at a time, and the
seconds it took written to the usage ledger, because a free model is still
work somebody may want to see the amount of.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.deps import CurrentUser, DbSession
from app.schemas.chat import Transcription
from app.services import transcribe as transcribe_service
from app.services.credits import record_units

log = logging.getLogger(__name__)

router = APIRouter(tags=["transcriptions"])

#: What the shim reports the seconds under, and what the usage screen shows.
STT_MODEL = "whisper"


@router.post("/transcriptions", response_model=Transcription)
async def transcribe(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
    language: str | None = Form(None),
):
    """Audio → text, for pasting into the composer. Not stored.

    The recording is not kept: it is a way of typing, and the transcript is
    what the person then reads, edits and sends. Refused with the service's
    own sentence when the backend is off or the clip is too long.
    """
    if not await transcribe_service.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="dictation_unavailable"
        )
    # `ko`/`en` pins the language; anything else — including nothing — lets
    # Whisper hear which of the two it is.
    pinned = language if language in transcribe_service.SPOKEN else None
    data = await file.read()
    try:
        text, seconds = await transcribe_service.transcribe_with_duration(
            data, file.filename or "speech.webm", pinned
        )
    except transcribe_service.TranscribeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if seconds:
        record_units(
            db, user, reason="speech.transcribe", model=STT_MODEL, units=seconds, unit="seconds"
        )
        await db.commit()
    return Transcription(text=text, seconds=seconds)
