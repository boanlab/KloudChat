"""Speech/music via chat/completions audio modality (stream required); speech PCM wrapped as WAV."""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import re
import struct
import uuid
import wave
from dataclasses import dataclass

import httpx

from app.services import files as file_service

log = logging.getLogger(__name__)

#: speech is real-time
_TIMEOUT = 240.0

#: GPT Audio PCM format, for the WAV header
_PCM_RATE = 24_000
_PCM_CHANNELS = 1
_PCM_BITS = 16

VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")

#: self-hosted Qwen3-TTS CustomVoice presets (`/v1/audio/voices`); the server's own default is
#: vivian, ours is sohee, its Korean voice
QWEN_VOICES = (
    "sohee", "vivian", "serena", "uncle_fu", "dylan", "eric", "ryan", "aiden", "ono_anna",
)

#: voice used when the person has not picked one the model has
_DEFAULT_VOICE = {"qwen3-tts": "sohee"}

_HANGUL = re.compile(r"[\uac00-\ud7a3\u3131-\u318e]")
_LETTER = re.compile(r"[^\W\d_]")


def _family(model_id: str) -> str:
    """`strict-local/qwen3-tts` → `qwen3-tts`."""
    return model_id.rsplit("/", 1)[-1].lower()


def voices_for(model_id: str, *, speech_endpoint: bool = False) -> tuple[str, ...]:
    """Voices of one narration model; empty when the model is not a narration one."""
    if _family(model_id).startswith("qwen3-tts"):
        return QWEN_VOICES
    if speech_endpoint:
        return ()  # a speech server whose voices we do not know: none offered
    return VOICES if "gpt-audio" in model_id else ()


def default_voice(model_id: str) -> str:
    voices = voices_for(model_id, speech_endpoint=True)
    return _DEFAULT_VOICE.get(_family(model_id)) or (voices[0] if voices else "alloy")


def language_of(text: str) -> str:
    """`Korean` when Hangul is a real share of the letters, else the server's `Auto`."""
    letters = len(_LETTER.findall(text))
    if letters and len(_HANGUL.findall(text)) / letters >= 0.3:
        return "Korean"
    return "Auto"


class AudioError(RuntimeError):
    """Generation failed; user-facing message."""


@dataclass(slots=True)
class GeneratedAudio:
    data: bytes
    mime: str
    extension: str
    input_tokens: int
    output_tokens: int
    #: transcript; empty when none sent
    transcript: str


def _wav(pcm: bytes) -> bytes:
    """44-byte RIFF header around raw PCM."""
    byte_rate = _PCM_RATE * _PCM_CHANNELS * _PCM_BITS // 8
    block_align = _PCM_CHANNELS * _PCM_BITS // 8
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack(
            "<IHHIIHH", 16, 1, _PCM_CHANNELS, _PCM_RATE, byte_rate, block_align, _PCM_BITS
        )
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def compose_prompt(prompt: str, *, speech: bool, seconds: int = 0) -> str:
    """Prompt as sent; length asked in words (no length parameter)."""
    text = prompt.strip()
    if seconds > 0:
        text += f" 약 {seconds}초 분량으로 읽어라." if speech else f" 길이는 약 {seconds}초."
    return text


async def generate(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    speech: bool,
    voice: str = "alloy",
    seconds: int = 0,
    speech_endpoint: bool = False,
) -> GeneratedAudio:
    """One clip, or AudioError. `speech_endpoint`: a model served at /audio/speech."""
    if speech_endpoint:
        return await _speech(
            base_url=base_url, api_key=api_key, model=model, prompt=prompt, voice=voice
        )
    payload: dict = {
        "model": model,
        "modalities": ["text", "audio"],
        "stream": True,
        "stream_options": {"include_usage": True},  # else no usage in the stream
        "messages": [
            {"role": "user", "content": compose_prompt(prompt, speech=speech, seconds=seconds)}
        ],
    }
    if speech:
        payload["audio"] = {"voice": voice if voice in VOICES else "alloy", "format": "pcm16"}

    chunks: list[bytes] = []
    transcript: list[str] = []
    usage: dict = {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:200]
                    log.warning("audio generation %s: %s", response.status_code, body)
                    raise AudioError("오디오를 만들지 못했습니다. 잠시 후 다시 시도하세요.")
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if event.get("usage"):
                        usage = event["usage"]
                    for choice in event.get("choices") or []:
                        audio = (choice.get("delta") or {}).get("audio") or {}
                        if audio.get("data"):
                            try:
                                chunks.append(base64.b64decode(audio["data"]))
                            except (binascii.Error, ValueError):
                                continue
                        if audio.get("transcript"):
                            transcript.append(audio["transcript"])
    except httpx.HTTPError as exc:
        log.warning("audio request failed: %s", exc)
        raise AudioError("오디오 생성 서버에 연결하지 못했습니다.") from exc

    body = b"".join(chunks)
    if not body:
        raise AudioError("오디오를 만들지 못했습니다. 잠시 뒤 다시 시도하세요.")

    if speech:
        data, mime, extension = _wav(body), "audio/wav", "wav"
    else:
        # Lyria sends no format; sniff the container
        if body[:3] == b"ID3" or body[:2] == b"\xff\xfb":
            data, mime, extension = body, "audio/mpeg", "mp3"
        elif body[:4] == b"RIFF":
            data, mime, extension = body, "audio/wav", "wav"
        elif body[:4] == b"OggS":
            data, mime, extension = body, "audio/ogg", "ogg"
        else:
            raise AudioError("오디오 형식을 알아보지 못했습니다.")

    return GeneratedAudio(
        data=data,
        mime=mime,
        extension=extension,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        transcript="".join(transcript).strip(),
    )


async def _speech(
    *, base_url: str, api_key: str, model: str, prompt: str, voice: str
) -> GeneratedAudio:
    """OpenAI-style /audio/speech (self-hosted TTS): the text is read as written, WAV back."""
    payload = {
        "model": model,
        "input": prompt.strip(),
        "voice": voice,
        "language": language_of(prompt),
        "response_format": "wav",
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/audio/speech",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        log.warning("speech request failed: %s", exc)
        raise AudioError("오디오 생성 서버에 연결하지 못했습니다.") from exc
    if response.status_code >= 400:
        log.warning("speech generation %s: %s", response.status_code, response.text[:200])
        raise AudioError("오디오를 만들지 못했습니다. 잠시 후 다시 시도하세요.")
    body = response.content
    if body[:4] != b"RIFF":
        raise AudioError("오디오를 만들지 못했습니다. 잠시 뒤 다시 시도하세요.")
    return GeneratedAudio(
        data=body, mime="audio/wav", extension="wav", input_tokens=0, output_tokens=0,
        transcript="",
    )


def duration_seconds(audio: GeneratedAudio) -> int:
    """Exact for WAV (header read, else our fixed PCM layout); 0 otherwise."""
    if audio.mime != "audio/wav":
        return 0
    try:
        with wave.open(io.BytesIO(audio.data)) as clip:
            if clip.getframerate() > 0 and clip.getnframes() > 0:
                return round(clip.getnframes() / clip.getframerate())
    except (wave.Error, EOFError):
        pass
    samples = max(0, len(audio.data) - 44)
    return round(samples / (_PCM_RATE * _PCM_CHANNELS * _PCM_BITS // 8))


def store(user_id: str, audio: GeneratedAudio) -> tuple[str, str]:
    file_id = uuid.uuid4().hex
    key = file_service.write_blob(user_id, file_id, f"audio.{audio.extension}", audio.data)
    return file_id, key


__all__ = [
    "QWEN_VOICES",
    "VOICES",
    "AudioError",
    "GeneratedAudio",
    "compose_prompt",
    "default_voice",
    "duration_seconds",
    "generate",
    "language_of",
    "store",
    "voices_for",
]
