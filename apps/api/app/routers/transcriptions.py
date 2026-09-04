"""Dictation: a recording in, its transcript out. Seconds are written to the usage ledger."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.deps import CurrentUser, DbSession
from app.schemas.chat import Transcription
from app.services import transcribe as transcribe_service
from app.services.credits import record_units

log = logging.getLogger(__name__)

router = APIRouter(tags=["transcriptions"])

#: Model name the usage ledger records seconds under.
STT_MODEL = "whisper"


@router.post("/transcriptions", response_model=Transcription)
async def transcribe(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
    language: str | None = Form(None),
    #: Recent conversation text, as a vocabulary hint for Whisper.
    prompt: str = Form("", max_length=500),
):
    """Audio → text for the composer. The recording is not stored."""
    if not await transcribe_service.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="dictation_unavailable"
        )
    # Only `ko`/`en` pin the language; otherwise Whisper detects it.
    pinned = language if language in transcribe_service.SPOKEN else None
    data = await file.read()
    try:
        text, seconds = await transcribe_service.transcribe_with_duration(
            data, file.filename or "speech.webm", pinned, prompt
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
