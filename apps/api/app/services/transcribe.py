"""Speech to text via internal Whisper only; audio never leaves the deployment, no fallback."""

from __future__ import annotations

import logging

import httpx

from app.services import settings_store, tools_gateway

log = logging.getLogger(__name__)

_TIMEOUT = 180.0

MAX_BYTES = 25 * 1024 * 1024

#: Whisper reads the prompt as the text just before the clip; a long one (a whole earlier answer)
#: can leave a short clip with no transcript at all, so only its tail is sent
PROMPT_MAX = 120


class TranscribeError(RuntimeError):
    """Transcription failure; user-facing message. `code`: `no_sound` (empty upload),
    `no_speech` (Whisper heard nothing), `too_long`, `failed` (the speech server)."""

    def __init__(self, message: str, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


async def available() -> bool:
    return bool((await settings_store.tools_config()).stt)


async def transcribe_with_duration(
    data: bytes, filename: str = "speech.webm", language: str | None = None, prompt: str = ""
) -> tuple[str, int]:
    """Transcript plus reported audio seconds (0 if unknown)."""
    stt_url = await _ready(data)
    return await _transcribe_timed(stt_url, data, filename, language, prompt)


async def transcribe(
    data: bytes,
    filename: str = "speech.webm",
    language: str | None = None,
    prompt: str = "",
) -> str:
    """Audio bytes to text; `language` None = auto, `prompt` = vocabulary hint."""
    stt_url = await _ready(data)
    return await _transcribe_locally(stt_url, data, filename, language, prompt)


async def _ready(data: bytes) -> str:
    """The speech server's address, after the recording itself is checked."""
    if len(data) > MAX_BYTES:
        raise TranscribeError("녹음이 너무 깁니다. 나눠서 말해 주세요.", "too_long")
    if not data:
        raise TranscribeError("녹음된 소리가 없습니다.", "no_sound")
    stt_url = (await settings_store.tools_config()).stt
    if not stt_url:
        raise TranscribeError(
            "내부 음성 인식(Whisper) 서버가 연결되지 않아 받아쓸 수 없습니다. "
            "녹음은 외부로 보내지 않습니다."
        )
    return stt_url


#: expected languages; other detections retried pinned to `_HOME`
SPOKEN = ("ko", "en")

_HOME = "ko"


async def _transcribe_locally(
    stt_url: str, data: bytes, filename: str, language: str | None = None, prompt: str = ""
) -> str:
    return (await _transcribe_timed(stt_url, data, filename, language, prompt))[0]


async def _transcribe_timed(
    stt_url: str, data: bytes, filename: str, language: str | None = None, prompt: str = ""
) -> tuple[str, int]:
    """Whisper via vLLM's OpenAI-shaped endpoint (`None` language = auto-detect): the text and
    the audio seconds it reported, kept per call so two requests never share a reading."""
    body = await _whisper(stt_url, data, filename, language, prompt)
    heard = str(body.get("language") or "").lower()[:2]
    if language is None and heard and heard not in SPOKEN:
        log.info("whisper heard %s; retrying pinned to %s", heard, _HOME)
        body = await _whisper(stt_url, data, filename, _HOME, prompt)

    seconds = 0
    usage = body.get("usage") or {}
    if isinstance(usage, dict) and usage.get("type") == "duration":
        seconds = int(usage.get("seconds") or 0)
    elif isinstance(body.get("duration"), (int, float)):
        seconds = int(round(float(body["duration"])))
    text = str(body.get("text") or "").strip()
    if not text and prompt.strip():
        # A vocabulary hint can leave Whisper with nothing to say for a short clip; once more
        # without it.
        log.info("whisper returned no text with a prompt; retrying without it")
        body = await _whisper(stt_url, data, filename, language)
        text = str(body.get("text") or "").strip()
    if not text:
        raise TranscribeError("들린 말이 없습니다.", "no_speech")
    return text, seconds


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
        form["prompt"] = prompt.strip()[-PROMPT_MAX:]
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
        log.warning("whisper answered %s", response.status_code)
        raise TranscribeError("받아쓰지 못했습니다. 잠시 뒤 다시 시도하세요.")
    try:
        body = response.json()
    except ValueError:
        return {"text": response.text or ""}
    return body if isinstance(body, dict) else {}


__all__ = ["MAX_BYTES", "TranscribeError", "available", "segments_of", "transcribe"]
