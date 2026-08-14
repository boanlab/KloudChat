"""Speech to text, for the composer's microphone.

**Not `webkitSpeechRecognition`**, which needs no backend but streams the
microphone to a third party. Audio goes to the same Whisper backend the YouTube
connector uses.

**Two paths, not equivalent.** Local Whisper keeps the audio inside the
cluster. Where vLLM cannot serve Whisper — the aarch64 build cannot — STT is
delegated to OpenRouter and the audio leaves the deployment. That is a separate
setting (`stt_or_model`) an operator can empty out, so dictation disappears on
those architectures rather than silently going off-premises. Local always wins
when both are configured.
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

#: A dictated note, not a lecture recording — a few minutes of speech on a slow
#: GPU queue.
_TIMEOUT = 180.0

#: Refused before upload. A phone-quality minute is well under it.
MAX_BYTES = 25 * 1024 * 1024


class TranscribeError(RuntimeError):
    """The message is written for the person who pressed the button."""


async def available() -> bool:
    return bool((await settings_store.tools_config()).stt or settings.stt_or_model)


async def transcribe(data: bytes, filename: str = "speech.webm") -> str:
    """Audio bytes → text. Raises `TranscribeError` with something readable."""
    if not await available():
        raise TranscribeError("음성 인식 백엔드가 설정되지 않았습니다.")
    if len(data) > MAX_BYTES:
        raise TranscribeError("녹음이 너무 깁니다. 나눠서 말해 주세요.")
    if not data:
        raise TranscribeError("녹음된 소리가 없습니다.")

    stt_url = (await settings_store.tools_config()).stt
    if not stt_url:
        return await _transcribe_via_openrouter(data, filename)

    url = f"{stt_url.rstrip('/')}/v1/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                url,
                files={"file": (filename, data, "application/octet-stream")},
                # Pinned: left to guess, a short Korean clip comes back as
                # confident nonsense in another language.
                data={"response_format": "json", "language": "ko"},
            )
    except httpx.HTTPError as exc:
        log.warning("whisper unreachable: %s", exc)
        raise TranscribeError("음성 인식 서버에 연결하지 못했습니다.") from exc

    if response.status_code >= 400:
        log.warning("whisper %s: %s", response.status_code, response.text[:200])
        raise TranscribeError("받아쓰지 못했습니다.")

    try:
        text = (response.json() or {}).get("text") or ""
    except ValueError:
        text = response.text or ""
    text = text.strip()
    if not text:
        raise TranscribeError("들린 말이 없습니다.")
    return text


#: What the model must emit for silence. A sentinel rather than "" because an
#: empty completion is indistinguishable from a dropped response.
_NO_SPEECH = "NO_SPEECH"

#: Audio MIME by extension, for the data: URI the chat/completions path needs.
#: Anything unrecognised is declared webm rather than refused — the model sniffs
#: the container anyway.
_AUDIO_MIME = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4",
    "ogg": "audio/ogg", "webm": "audio/webm", "flac": "audio/flac",
}


async def _transcribe_via_openrouter(data: bytes, filename: str) -> str:
    """Transcribe through LiteLLM when no local Whisper exists.

    OpenRouter serves neither `/v1/audio/transcriptions` nor a Whisper model, so
    this goes through `chat/completions` with an `input_audio` part. The prompt
    pins the job down: an audio-capable chat model will otherwise summarise or
    answer the clip, and a summary returned as a transcript is indistinguishable
    from one downstream.
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    b64 = base64.b64encode(data).decode()
    payload = {
        "model": settings.stt_or_model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": (
                    "이 오디오를 그대로 받아써 주세요. 한국어입니다. "
                    "요약하거나 설명하지 말고, 들린 말만 텍스트로 옮기세요. "
                    f"들린 말이 없으면 다른 말 없이 {_NO_SPEECH} 만 출력하세요."
                )},
                {"type": "input_audio", "input_audio": {
                    "data": b64,
                    "format": _AUDIO_MIME.get(ext, "audio/webm").split("/", 1)[1],
                }},
            ],
        }],
        "temperature": 0,
        # Mistral rejects temperature=0 unless top_p is 1 (code 3054). Greedy
        # is what a transcript wants, so both are pinned.
        "top_p": 1,
    }
    headers = {"Authorization": f"Bearer {settings.litellm_master_key}"}
    url = f"{settings.litellm_base_url.rstrip('/')}/v1/chat/completions"
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
    # A chat model asked to transcribe silence describes it instead, and the
    # description would land in the composer as words nobody said. The sentinel
    # makes that case detectable.
    if not text or text.strip(' ."\'') == _NO_SPEECH:
        raise TranscribeError("들린 말이 없습니다.")
    return text


__all__ = ["MAX_BYTES", "TranscribeError", "available", "transcribe"]
