"""Speech to text: local Whisper first, chat-model fallback via LiteLLM when `stt_or_model` is set.

Audio leaves the deployment only on the fallback path; an empty `stt_or_model` disables it.
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

_TIMEOUT = 180.0

#: Checked before upload.
MAX_BYTES = 25 * 1024 * 1024


class TranscribeError(RuntimeError):
    """Transcription failure with a user-facing message."""


async def available() -> bool:
    return bool((await settings_store.tools_config()).stt or settings.stt_or_model)


async def transcribe_with_duration(
    data: bytes, filename: str = "speech.webm", language: str | None = None, prompt: str = ""
) -> tuple[str, int]:
    """Transcript plus the audio seconds the backend reported (0 when unknown)."""
    text = await transcribe(data, filename, language, prompt)
    return text, int(_last_seconds)


#: Seconds reported by the last local transcription.
_last_seconds = 0


async def transcribe(
    data: bytes,
    filename: str = "speech.webm",
    language: str | None = None,
    prompt: str = "",
) -> str:
    """Audio bytes to text; raises `TranscribeError`.

    `language`: ISO code to pin, or None to auto-detect within `SPOKEN`.
    `prompt`: recent conversation text, passed to Whisper as a vocabulary hint.
    """
    if not await available():
        raise TranscribeError("음성 인식 백엔드가 설정되지 않았습니다.")
    if len(data) > MAX_BYTES:
        raise TranscribeError("녹음이 너무 깁니다. 나눠서 말해 주세요.")
    if not data:
        raise TranscribeError("녹음된 소리가 없습니다.")

    stt_url = (await settings_store.tools_config()).stt
    if not stt_url:
        return await _transcribe_via_openrouter(data, filename, language)

    try:
        return await _transcribe_locally(stt_url, data, filename, language, prompt)
    except TranscribeError:
        # A configured `stt` URL may not be able to answer (no Whisper on aarch64).
        if not settings.stt_or_model:
            raise
        log.warning("local whisper failed; falling through to %s", settings.stt_or_model)
        return await _transcribe_via_openrouter(data, filename, language)


#: Expected languages; a detection outside this pair is retried pinned to `_HOME`.
SPOKEN = ("ko", "en")

_HOME = "ko"


async def _transcribe_locally(
    stt_url: str, data: bytes, filename: str, language: str | None = None, prompt: str = ""
) -> str:
    """Whisper via vLLM's OpenAI-shaped endpoint; `None` language auto-detects."""
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


async def _whisper(
    stt_url: str, data: bytes, filename: str, language: str | None, prompt: str = ""
) -> dict:
    """One Whisper pass; `verbose_json` so the response names the detected language."""
    url = f"{stt_url.rstrip('/')}/v1/audio/transcriptions"
    form: dict[str, str] = {"response_format": "verbose_json"}
    if language:
        form["language"] = language
    if prompt.strip():
        form["prompt"] = prompt.strip()[:500]
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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


#: Sentinel the chat-model fallback must emit for silence.
_NO_SPEECH = "NO_SPEECH"

#: Audio MIME by extension; unknown extensions are sent as webm.
_AUDIO_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "flac": "audio/flac",
}


_TONGUE = {"ko": "한국어입니다.", "en": "영어입니다."}
_EITHER = "한국어 또는 영어이며, 들린 언어 그대로 적으세요."


async def _transcribe_via_openrouter(
    data: bytes, filename: str, language: str | None = None
) -> str:
    """Transcribe via LiteLLM `chat/completions` with an `input_audio` part.

    The prompt forbids summarising: an audio-capable chat model otherwise answers the clip.
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    b64 = base64.b64encode(data).decode()
    payload = {
        "model": settings.stt_or_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "이 오디오를 그대로 받아써 주세요. "
                            + _TONGUE.get(language or "", _EITHER)
                            + " 요약하거나 설명하지 말고, 들린 말만 텍스트로 옮기세요. "
                            f"들린 말이 없으면 다른 말 없이 {_NO_SPEECH} 만 출력하세요."
                        ),
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": b64,
                            "format": _AUDIO_MIME.get(ext, "audio/webm").split("/", 1)[1],
                        },
                    },
                ],
            }
        ],
        "temperature": 0,
        # Mistral rejects temperature=0 unless top_p is 1.
        "top_p": 1,
    }
    base, key = await settings_store.litellm_config()
    if not base:
        raise TranscribeError("음성 인식에 쓸 모델 주소가 설정되지 않았습니다.")
    headers = {"Authorization": f"Bearer {key}"}
    url = f"{base.rstrip('/')}/v1/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("stt via openrouter unreachable: %s", exc)
        raise TranscribeError("음성 인식 서버에 연결하지 못했습니다.") from exc
    if response.status_code >= 400:
        log.warning("stt via openrouter %s: %s", response.status_code, response.text[:200])
        raise TranscribeError("받아쓰지 못했습니다.")
    try:
        choices = (response.json() or {}).get("choices") or [{}]
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
    except (ValueError, AttributeError, IndexError):
        text = ""
    if not text or text.strip(" .\"'") == _NO_SPEECH:
        raise TranscribeError("들린 말이 없습니다.")
    return text


__all__ = ["MAX_BYTES", "TranscribeError", "available", "transcribe"]
