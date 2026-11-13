"""Speech to text via internal Whisper only; audio never leaves the deployment, no fallback."""

from __future__ import annotations

import logging

import httpx

from app.services import settings_store, tools_gateway

log = logging.getLogger(__name__)

_TIMEOUT = 180.0

MAX_BYTES = 25 * 1024 * 1024


class TranscribeError(RuntimeError):
    """Transcription failure; user-facing message."""


async def available() -> bool:
    return bool((await settings_store.tools_config()).stt)


async def transcribe_with_duration(
    data: bytes, filename: str = "speech.webm", language: str | None = None, prompt: str = ""
) -> tuple[str, int]:
    """Transcript plus reported audio seconds (0 if unknown)."""
    text = await transcribe(data, filename, language, prompt)
    return text, int(_last_seconds)


#: seconds from the last local transcription
_last_seconds = 0


async def transcribe(
    data: bytes,
    filename: str = "speech.webm",
    language: str | None = None,
    prompt: str = "",
) -> str:
    """Audio bytes to text; `language` None = auto, `prompt` = vocabulary hint."""
    if len(data) > MAX_BYTES:
        raise TranscribeError("녹음이 너무 깁니다. 나눠서 말해 주세요.")
    if not data:
        raise TranscribeError("녹음된 소리가 없습니다.")

    stt_url = (await settings_store.tools_config()).stt
    if not stt_url:
        raise TranscribeError(
            "내부 음성 인식(Whisper) 서버가 연결되지 않아 받아쓸 수 없습니다. "
            "녹음은 외부로 보내지 않습니다."
        )
    return await _transcribe_locally(stt_url, data, filename, language, prompt)


#: expected languages; other detections retried pinned to `_HOME`
SPOKEN = ("ko", "en")

_HOME = "ko"


async def _transcribe_locally(
    stt_url: str, data: bytes, filename: str, language: str | None = None, prompt: str = ""
) -> str:
    """Whisper via vLLM's OpenAI-shaped endpoint; `None` language = auto-detect."""
    body = await _whisper(stt_url, data, filename, language, prompt)
    heard = str(body.get("language") or "").lower()[:2]
    if language is None and heard and heard not in SPOKEN:
        log.info("whisper heard %s; retrying pinned to %s", heard, _HOME)
        body = await _whisper(stt_url, data, filename, _HOME, prompt)

    global _last_seconds
    _last_seconds = 0
    usage = body.get("usage") or {}
    if isinstance(usage, dict) and usage.get("type") == "duration":
        _last_seconds = int(usage.get("seconds") or 0)
    elif isinstance(body.get("duration"), (int, float)):
        _last_seconds = int(round(float(body["duration"])))
    text = str(body.get("text") or "").strip()
    if not text:
        raise TranscribeError("들린 말이 없습니다.")
    return text


async def segments_of(
    data: bytes, filename: str = "piece.flac", language: str | None = None
) -> tuple[list[dict], str]:
    """A piece of a longer recording (`video_jobs`): Whisper's timed segments `{start, end,
    text}`, seconds from the piece's start, and the language heard. Silence gives no segments,
    not an error; any detected language stands (a whole piece is long enough to tell)."""
    if len(data) > MAX_BYTES:
        raise TranscribeError("녹음 조각이 너무 큽니다.")
    stt_url = (await settings_store.tools_config()).stt
    if not stt_url:
        raise TranscribeError("내부 음성 인식(Whisper) 서버가 연결되지 않았습니다.")
    body = await _whisper(stt_url, data, filename, language, segments=True)
    heard = str(body.get("language") or language or "").lower()[:8]
    raw = body.get("segments")
    found: list[dict] = []
    if isinstance(raw, list):
        for segment in raw:
            if not isinstance(segment, dict):
                continue
            text = " ".join(str(segment.get("text") or "").split())
            if text:
                found.append({"start": segment.get("start") or 0.0,
                              "end": segment.get("end") or segment.get("start") or 0.0,
                              "text": text})
    if not found and (text := " ".join(str(body.get("text") or "").split())):
        # no timings from this server: the piece's text at the piece's start
        found = [{"start": 0.0, "end": float(body.get("duration") or 0.0), "text": text,
                  "whole": True}]
    return found, heard


async def _whisper(
    stt_url: str, data: bytes, filename: str, language: str | None, prompt: str = "",
    *, segments: bool = False,
) -> dict:
    """One Whisper pass; `verbose_json` for the detected language."""
    url = f"{stt_url.rstrip('/')}/v1/audio/transcriptions"
    form: dict[str, str] = {"response_format": "verbose_json"}
    if segments:
        form["timestamp_granularities[]"] = "segment"
    if language:
        form["language"] = language
    if prompt.strip():
        form["prompt"] = prompt.strip()[:500]
    try:
        # a ten-minute piece of a long recording takes longer than a dictation
        async with tools_gateway.client(timeout=600.0 if segments else _TIMEOUT) as client:
            response = await client.post(
                url, files={"file": (filename, data, "application/octet-stream")}, data=form
            )
    except httpx.HTTPError as exc:
        log.warning("whisper unreachable: %s", exc)
        raise TranscribeError("음성 인식 서버에 연결하지 못했습니다.") from exc
    if response.status_code >= 400:
        log.warning("whisper %s: %s", response.status_code, response.text[:200])
        raise TranscribeError("받아쓰지 못했습니다.")
    try:
        body = response.json()
    except ValueError:
        return {"text": response.text or ""}
    return body if isinstance(body, dict) else {}


__all__ = ["MAX_BYTES", "TranscribeError", "available", "segments_of", "transcribe"]
