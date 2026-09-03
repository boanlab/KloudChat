"""Speech to text, for the composer's microphone.

**Not `webkitSpeechRecognition`**, which needs no backend but streams the
microphone to a third party. Audio goes to the same Whisper backend the YouTube
connector uses.

**Two paths, not equivalent.** Local Whisper keeps the audio inside the
cluster. Where vLLM cannot serve Whisper — the aarch64 build cannot — STT is
delegated to OpenRouter and the audio leaves the deployment. That is a separate
setting (`stt_or_model`) an operator can empty out, so transcription disappears
on those architectures rather than silently going off-premises.

Local is tried first and, when it fails, the other path is taken *if the
operator has opened it*. An ARM deployment carrying the same configuration as
an x86 one has an `stt` address pointing at something that cannot answer, and
treating a configured address as a working one turned that into a hard failure
for every recording. An empty `stt_or_model` still means no, and then the local
failure stands where it is.
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


async def transcribe_with_duration(
    data: bytes, filename: str = "speech.webm", language: str | None = None, prompt: str = ""
) -> tuple[str, int]:
    """`(text, seconds)` — the transcript and how much audio the shim reported.

    The seconds are what the usage ledger records for a model that costs no
    credits. Zero when the backend did not say.
    """
    text = await transcribe(data, filename, language, prompt)
    return text, int(_last_seconds)


#: Seconds the shim reported for the last local transcription. Whisper's
#: `usage: {"type": "duration", "seconds": n}` is the only measure of its work.
_last_seconds = 0


async def transcribe(
    data: bytes,
    filename: str = "speech.webm",
    language: str | None = None,
    prompt: str = "",
) -> str:
    """Audio bytes → text. Raises `TranscribeError` with something readable.

    `language` is an ISO code to pin (`ko`, `en`) or `None` to let the model
    hear which of `SPOKEN` it is. `prompt` is what the conversation was about
    — the last thing said back — which Whisper reads as a hint for vocabulary
    and spelling: 「some tennis」 heard in a chat about tennis stays tennis.
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
        # Local first, and local failing is not the end of it.
        #
        # vLLM serves Whisper on amd64 and does not on aarch64, so an ARM
        # deployment that carries the same configuration as an x86 one has an
        # `stt` URL pointing at something that cannot answer. Before this, that
        # was a hard failure: the address was set, so the address was used, and
        # every recording came back 받아쓰지 못했습니다.
        #
        # Falling through is not a privacy decision made here. Setting
        # `stt_or_model` *is* that decision — an operator who does not want
        # audio leaving the cluster leaves it empty, and then there is nothing
        # to fall through to and the local failure stands.
        if not settings.stt_or_model:
            raise
        log.warning("local whisper failed; falling through to %s", settings.stt_or_model)
        return await _transcribe_via_openrouter(data, filename, language)


#: The languages people here dictate in. Whisper is left to hear which one it
#: is; a guess outside this pair is the failure mode the old pin guarded
#: against — a short Korean clip coming back as confident nonsense in another
#: language — and is retried pinned to Korean.
SPOKEN = ("ko", "en")

#: What the retry is pinned to when the guess is neither.
_HOME = "ko"


async def _transcribe_locally(
    stt_url: str, data: bytes, filename: str, language: str | None = None, prompt: str = ""
) -> str:
    """Whisper inside the cluster, through vLLM's OpenAI-shaped endpoint.

    `language` pins the pass; `None` lets Whisper detect it, so that an
    English sentence spoken to the 영어회화 튜터 is written in English and a
    Korean one in Korean, and only a guess outside `SPOKEN` is redone pinned.
    """
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
    """One pass; `verbose_json` so the answer says which language it heard."""
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


#: What the model must emit for silence. A sentinel rather than "" because an
#: empty completion is indistinguishable from a dropped response.
_NO_SPEECH = "NO_SPEECH"

#: Audio MIME by extension, for the data: URI the chat/completions path needs.
#: Anything unrecognised is declared webm rather than refused — the model sniffs
#: the container anyway.
_AUDIO_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "flac": "audio/flac",
}


#: How the chat-model fallback is told what it is listening to.
_TONGUE = {"ko": "한국어입니다.", "en": "영어입니다."}
_EITHER = "한국어 또는 영어이며, 들린 언어 그대로 적으세요."


async def _transcribe_via_openrouter(
    data: bytes, filename: str, language: str | None = None
) -> str:
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
        # Mistral rejects temperature=0 unless top_p is 1 (code 3054). Greedy
        # is what a transcript wants, so both are pinned.
        "top_p": 1,
    }
    # Resolved the way every other model call resolves it.
    #
    # This read `settings.litellm_base_url` — the raw environment variable —
    # and nothing sets that. Everywhere else the address comes from
    # `settings_store.litellm_config`, which takes the operator's setting
    # first, derives one from the backend address when there is none, and only
    # then falls back to the environment. So the whole deployment could be
    # talking to LiteLLM perfectly well while this one path built
    # `/v1/chat/completions` with nothing in front of it and reported the
    # backend unreachable.
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
    # A chat model asked to transcribe silence describes it instead, and the
    # description would land in the composer as words nobody said. The sentinel
    # makes that case detectable.
    if not text or text.strip(" .\"'") == _NO_SPEECH:
        raise TranscribeError("들린 말이 없습니다.")
    return text


__all__ = ["MAX_BYTES", "TranscribeError", "available", "transcribe"]
